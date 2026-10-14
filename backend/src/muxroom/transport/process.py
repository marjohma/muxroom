"""Local process helpers shared by the local and OpenSSH transports."""

import asyncio
import contextlib
import errno
import fcntl
import os
import signal
import struct
import sys
import termios
from collections.abc import Mapping, Sequence

from muxroom.errors import MuxroomError
from muxroom.transport.base import CommandResult

READ_CHUNK = 64 * 1024
CLOSE_GRACE_SECONDS = 1.0

# Child-side helper: make the PTY the controlling terminal (needed for
# SIGWINCH delivery on macOS/BSD), then exec the real program. Avoids
# `preexec_fn`, which is unsafe in multi-threaded processes.
_CTTY_HELPER = (
    "import fcntl, os, signal, sys, termios\n"
    "signal.pthread_sigmask(signal.SIG_SETMASK, [])\n"  # never inherit blocked signals
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for spawned clients: never nested inside a parent tmux."""
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env.setdefault("LANG", "en_US.UTF-8")
    if extra:
        env.update(extra)
    return env


async def run_process(
    argv: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
) -> CommandResult:
    """Run argv without a shell. A missing executable yields exit status 127."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env) if env is not None else child_env(),
            start_new_session=True,
        )
    except FileNotFoundError:
        return CommandResult(127, "", f"{argv[0]}: command not found")
    except PermissionError as exc:
        return CommandResult(126, "", f"{argv[0]}: {exc}")
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise MuxroomError("timeout", f"{argv[0]} did not finish within {timeout:g}s") from None
    return CommandResult(
        proc.returncode if proc.returncode is not None else -1,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class PtyTerminal:
    """An interactive local program on a fresh PTY.

    Closing only closes the PTY master and, if needed, signals the *client*
    process (tmux client or ssh). The tmux server is never signalled.
    """

    def __init__(self, proc: asyncio.subprocess.Process, master_fd: int) -> None:
        self._proc = proc
        self._fd = master_fd
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self._closed = False
        self._eof = False
        self._reading = False
        self._start_reading()

    @classmethod
    async def spawn(
        cls,
        argv: Sequence[str],
        *,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> "PtyTerminal":
        master, slave = os.openpty()
        try:
            _set_winsize(master, cols, rows)
            os.set_blocking(master, False)
            term_env = child_env({"TERM": "xterm-256color", **(env or {})})
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-c",
                _CTTY_HELPER,
                *argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=term_env,
                start_new_session=True,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        return cls(proc, master)

    @property
    def pid(self) -> int:
        return self._proc.pid

    async def wait_exit(self) -> int:
        """Wait for the spawned program itself (not for PTY EOF) to exit."""
        return await self._proc.wait()

    def _start_reading(self) -> None:
        if not self._reading and not self._eof and not self._closed:
            self._loop.add_reader(self._fd, self._on_readable)
            self._reading = True

    def _stop_reading(self) -> None:
        if self._reading:
            self._loop.remove_reader(self._fd)
            self._reading = False

    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, READ_CHUNK)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno not in (errno.EIO, errno.EBADF):
                raise
            data = b""
        if not data:
            self._eof = True
            self._stop_reading()
            self._queue.put_nowait(b"")
            return
        self._queue.put_nowait(data)
        if self._queue.full():
            self._stop_reading()  # backpressure: resume once the consumer drains

    async def read(self) -> bytes:
        if self._closed and self._queue.empty():
            return b""
        data = await self._queue.get()
        if not self._queue.full():
            self._start_reading()
        return data

    async def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view and not self._closed:
            try:
                written = os.write(self._fd, view)
                view = view[written:]
            except BlockingIOError:
                ready = self._loop.create_future()
                self._loop.add_writer(self._fd, ready.set_result, None)
                try:
                    await ready
                finally:
                    self._loop.remove_writer(self._fd)
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF):
                    return
                raise

    async def resize(self, cols: int, rows: int) -> None:
        if not self._closed:
            _set_winsize(self._fd, cols, rows)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_reading()
        with contextlib.suppress(OSError):
            os.close(self._fd)
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(b"")
        await self._reap_client()

    async def _reap_client(self) -> None:
        """The client should exit on PTY hangup; escalate only for the client."""
        for sig in (None, signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
            if sig is not None:
                with contextlib.suppress(ProcessLookupError):
                    self._proc.send_signal(sig)
            try:
                await asyncio.wait_for(self._proc.wait(), CLOSE_GRACE_SECONDS)
                return
            except TimeoutError:
                continue
