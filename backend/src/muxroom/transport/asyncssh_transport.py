"""Pure-Python SSH transport (AsyncSSH) for hosts with key/agent auth."""

import asyncio
import contextlib
import shlex
from collections.abc import Sequence

import asyncssh

from muxroom.config import HostConfig
from muxroom.errors import MuxroomError
from muxroom.transport.base import CommandResult, TerminalConnection
from muxroom.transport.command_channel import (
    ChannelBroken,
    ChannelPool,
    ChannelStreams,
    CommandChannel,
)
from muxroom.transport.sftp_files import SftpFiles
from muxroom.transport.ssh_errors import classify_asyncssh

READ_CHUNK = 64 * 1024


class AsyncSshTerminal:
    def __init__(self, conn: asyncssh.SSHClientConnection, process: asyncssh.SSHClientProcess):
        self._conn = conn
        self._process = process
        self._closed = False

    async def read(self) -> bytes:
        if self._closed:
            return b""
        try:
            data = await self._process.stdout.read(READ_CHUNK)
        except (asyncssh.Error, OSError):
            return b""
        return data if isinstance(data, bytes) else data.encode()

    async def write(self, data: bytes) -> None:
        if self._closed:
            return
        with contextlib.suppress(asyncssh.Error, OSError, BrokenPipeError):
            self._process.stdin.write(data)
            await self._process.stdin.drain()

    async def resize(self, cols: int, rows: int) -> None:
        if not self._closed:
            with contextlib.suppress(asyncssh.Error, OSError):
                self._process.change_terminal_size(cols, rows)

    async def close(self) -> None:
        """Closing the channel makes sshd hang up the remote tmux client (detach)."""
        if self._closed:
            return
        self._closed = True
        self._process.close()
        self._conn.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._conn.wait_closed(), 5)


class AsyncSshTransport:
    def __init__(self, host: HostConfig) -> None:
        assert host.ssh_host is not None
        self.host_id = host.id
        self._host = host
        self._control: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()
        self._files = SftpFiles(host.id, self._start_sftp)
        self._channels = ChannelPool(self._open_channel)

    async def _open_channel(self) -> CommandChannel:
        return await CommandChannel.open(
            self._start_channel_process,
            timeout=self._host.connect_timeout + 5,
            on_start_failure=lambda stderr: MuxroomError(
                "ssh_error",
                stderr.strip() or "remote shell failed to start",
                host_id=self.host_id,
            ),
        )

    async def _start_channel_process(self) -> ChannelStreams:
        conn = await self._control_conn()
        try:
            process = await conn.create_process("sh", encoding=None)
        except (asyncssh.Error, OSError) as exc:
            self._control = None
            raise classify_asyncssh(exc, self.host_id) from exc

        async def close() -> None:
            process.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait_closed(), 2)

        async def stderr_text() -> str:
            data = await process.stderr.read()
            return data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)

        return ChannelStreams(process.stdin, process.stdout, process.stderr, close, stderr_text)

    def files(self) -> SftpFiles:
        return self._files

    async def _start_sftp(self):
        conn = await self.connect()  # dedicated file connection
        try:
            client = await conn.start_sftp_client()
        except BaseException as exc:
            conn.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise MuxroomError("sftp_unavailable", str(exc), host_id=self.host_id) from exc

        async def closer() -> None:
            client.exit()
            conn.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(conn.wait_closed(), 5)

        return client, closer

    async def connect(self) -> asyncssh.SSHClientConnection:
        options = {"connect_timeout": self._host.connect_timeout}
        if self._host.ssh_config:
            options["config"] = [self._host.ssh_config]
        try:
            return await asyncssh.connect(self._host.ssh_host, **options)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise classify_asyncssh(exc, self.host_id) from exc

    async def _control_conn(self) -> asyncssh.SSHClientConnection:
        async with self._lock:
            if self._control is None or self._control.is_closed():
                self._control = await self.connect()
            return self._control

    async def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        """Run through a persistent `sh` on the pooled control connection."""
        try:
            return await self._channels.run(shlex.join(argv), timeout=timeout)
        except ChannelBroken as exc:
            self._control = None
            raise MuxroomError(
                "host_unreachable", f"Connection lost ({exc})", host_id=self.host_id
            ) from exc

    async def open_terminal(
        self, argv: Sequence[str], *, cols: int, rows: int
    ) -> TerminalConnection:
        conn = await self.connect()  # one connection per terminal: isolated failures
        try:
            process = await conn.create_process(
                shlex.join(argv),
                term_type="xterm-256color",
                term_size=(cols, rows),
                encoding=None,
            )
        except BaseException as exc:
            conn.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise classify_asyncssh(exc, self.host_id) from exc
        return AsyncSshTerminal(conn, process)

    async def close(self) -> None:
        await self._channels.close()
        await self._files.close()
        if self._control is not None:
            self._control.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._control.wait_closed(), 5)
            self._control = None
