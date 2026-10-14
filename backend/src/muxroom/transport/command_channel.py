"""Run many commands through one long-lived remote `sh` (no per-command login).

Each remote command over plain SSH starts a new session and a login shell
that sources the user's rc files on every call. A channel instead keeps a
single `sh` reading commands from stdin and delimits every command's output
with random markers:

    { <command>
    } </dev/null; printf '\\n@@ad:%d:<token>@@\\n' "$?"; printf '\\n@@ad:<token>@@\\n' >&2

stdin of the command is /dev/null, so it can never consume the protocol
stream. Commands run one at a time; a timeout or broken stream kills the
channel, and the owner starts a fresh one for the next command.
"""

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from muxroom.errors import MuxroomError
from muxroom.transport.base import CommandResult

log = logging.getLogger(__name__)

READY = b"@@muxroom-ready@@"
IDLE_CLOSE_SECONDS = 300.0
MAX_REPLY_BYTES = 16 * 1024 * 1024
READ_CHUNK = 64 * 1024


class ByteReader(Protocol):
    async def read(self, n: int) -> bytes: ...


class ByteWriter(Protocol):
    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...


@dataclass(slots=True)
class ChannelStreams:
    stdin: ByteWriter
    stdout: ByteReader
    stderr: ByteReader
    close: Callable[[], Awaitable[None]]
    stderr_text: Callable[[], Awaitable[str]]  # diagnostics after a failed start


class ReplyTooLarge(Exception):
    pass


class _BoundedReader:
    """`readuntil` with a hard size cap, independent of the stream implementation."""

    def __init__(self, source: ByteReader, limit: int = MAX_REPLY_BYTES) -> None:
        self._source = source
        self._limit = limit
        self._buffer = bytearray()

    async def readuntil(self, separator: bytes) -> bytes:
        start = 0
        while True:
            index = self._buffer.find(separator, start)
            if index >= 0:
                end = index + len(separator)
                data = bytes(self._buffer[:end])
                del self._buffer[:end]
                return data
            if len(self._buffer) > self._limit:
                raise ReplyTooLarge(f"reply exceeds {self._limit} bytes")
            start = max(0, len(self._buffer) - len(separator) + 1)
            chunk = await self._source.read(READ_CHUNK)
            if not chunk:
                raise asyncio.IncompleteReadError(bytes(self._buffer), None)
            self._buffer += chunk if isinstance(chunk, bytes) else chunk.encode()


class ChannelBroken(Exception):
    """The channel can no longer be used (EOF, timeout, write failure).

    `retryable` is True only when the remote shell was already gone before
    the command could start (EOF without any output), so re-running it
    elsewhere cannot execute it twice.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class CommandChannel:
    def __init__(self, streams: ChannelStreams) -> None:
        self._s = streams
        self._out = _BoundedReader(streams.stdout)
        self._err = _BoundedReader(streams.stderr)
        self._lock = asyncio.Lock()
        self.broken = False

    @classmethod
    async def open(
        cls,
        start: Callable[[], Awaitable[ChannelStreams]],
        *,
        timeout: float,
        on_start_failure: Callable[[str], MuxroomError],
    ) -> "CommandChannel":
        streams = await start()
        channel = cls(streams)
        # Also drains whatever the login shell printed (motd, rc-file noise).
        out_task = asyncio.ensure_future(channel._out.readuntil(READY + b"\n"))
        err_task = asyncio.ensure_future(channel._err.readuntil(READY + b"\n"))
        try:
            streams.stdin.write(b"echo " + READY + b"; echo " + READY + b" >&2\n")
            await streams.stdin.drain()
            await asyncio.wait_for(asyncio.gather(out_task, err_task), timeout)
        except (TimeoutError, OSError, EOFError, asyncio.IncompleteReadError, ReplyTooLarge) as exc:
            for task in (out_task, err_task):
                task.cancel()
            diagnostics = ""
            if err_task.done() and not err_task.cancelled():
                err = err_task.exception()
                if isinstance(err, asyncio.IncompleteReadError):
                    diagnostics = err.partial.decode("utf-8", "replace")
            with contextlib.suppress(Exception):
                diagnostics += await asyncio.wait_for(streams.stderr_text(), 2)
            await channel.close()
            if isinstance(exc, TimeoutError) and not diagnostics.strip():
                raise MuxroomError("timeout", "Connection timed out") from exc
            raise on_start_failure(diagnostics) from exc
        return channel

    async def run(self, command: str, *, timeout: float) -> CommandResult:
        async with self._lock:
            if self.broken:
                raise ChannelBroken("channel already broken", retryable=True)
            token = secrets.token_hex(8)
            out_end = f":{token}@@\n".encode()
            err_end = f"@@ad:{token}@@\n".encode()
            script = (
                f"{{ {command}\n}} </dev/null; "
                f"printf '\\n@@ad:%d:{token}@@\\n' \"$?\"; "
                f"printf '\\n@@ad:{token}@@\\n' >&2\n"
            )
            out_task = asyncio.ensure_future(self._out.readuntil(out_end))
            err_task = asyncio.ensure_future(self._err.readuntil(err_end))
            try:
                self._s.stdin.write(script.encode())
                await self._s.stdin.drain()
                stdout, stderr = await asyncio.wait_for(asyncio.gather(out_task, err_task), timeout)
            except ReplyTooLarge:
                await self._break()
                raise MuxroomError(
                    "ssh_error", f"Command output exceeded {MAX_REPLY_BYTES // 2**20} MiB"
                ) from None
            except TimeoutError:
                await self._break()
                raise MuxroomError(
                    "timeout", f"Command did not finish within {timeout:g}s"
                ) from None
            except asyncio.IncompleteReadError as exc:
                await self._break()
                raise ChannelBroken("remote shell closed", retryable=exc.partial == b"") from exc
            except (OSError, EOFError, RuntimeError) as exc:
                await self._break()
                raise ChannelBroken(str(exc) or type(exc).__name__, retryable=True) from exc
            except Exception as exc:  # never leave a half-read channel in use
                await self._break()
                raise ChannelBroken(str(exc) or type(exc).__name__) from exc
            finally:
                for task in (out_task, err_task):
                    if not task.done():
                        task.cancel()
            body, _, tail = stdout[: -len(out_end)].rpartition(b"\n@@ad:")
            try:
                status = int(tail)
            except ValueError:
                await self._break()
                raise ChannelBroken("malformed command trailer") from None
            err_body = stderr[: -len(err_end)].removesuffix(b"\n")  # our leading newline
            return CommandResult(
                status, body.decode("utf-8", "replace"), err_body.decode("utf-8", "replace")
            )

    async def _break(self) -> None:
        self.broken = True
        await self.close()

    async def close(self) -> None:
        self.broken = True
        with contextlib.suppress(Exception):
            await self._s.close()


class ChannelPool:
    """Owns at most one channel; reopens on demand and closes when idle."""

    def __init__(
        self,
        opener: Callable[[], Awaitable[CommandChannel]],
        *,
        idle_close: float = IDLE_CLOSE_SECONDS,
    ) -> None:
        self._opener = opener
        self._idle_close = idle_close
        self._channel: CommandChannel | None = None
        self._open_lock = asyncio.Lock()
        self._idle: asyncio.TimerHandle | None = None

    async def _get(self) -> CommandChannel:
        async with self._open_lock:
            if self._channel is None or self._channel.broken:
                self._channel = await self._opener()
            return self._channel

    async def run(self, command: str, *, timeout: float) -> CommandResult:
        channel = await self._get()
        try:
            return await channel.run(command, timeout=timeout)
        except ChannelBroken as exc:
            if not exc.retryable:
                raise
            log.info("command channel was closed; reopening")
            channel = await self._get()
            return await channel.run(command, timeout=timeout)
        finally:
            self._touch()

    def _touch(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
        loop = asyncio.get_running_loop()
        self._idle = loop.call_later(self._idle_close, lambda: loop.create_task(self.close()))

    async def close(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        channel, self._channel = self._channel, None
        if channel is not None:
            await channel.close()
