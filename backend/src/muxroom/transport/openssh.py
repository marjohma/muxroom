"""Transport that drives the system OpenSSH client.

Honours the user's complete ssh_config (ControlMaster, ProxyJump, Match,
Kerberos, Keychain, ...). Every invocation runs with BatchMode=yes, so
muxroom never prompts for or sees credentials.
"""

import asyncio
import contextlib
import math
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
from muxroom.transport.process import PtyTerminal, child_env, run_process
from muxroom.transport.sftp_files import SftpFiles
from muxroom.transport.sftp_pipe import start_pipe_sftp_client
from muxroom.transport.ssh_errors import classify_openssh

STREAM_LIMIT = 16 * 1024 * 1024


def remote_command(argv: Sequence[str]) -> str:
    """The single place where argv becomes a string for the remote shell."""
    return shlex.join(argv)


class OpenSshTransport:
    def __init__(self, host: HostConfig, ssh_binary: str = "ssh") -> None:
        assert host.ssh_host is not None
        self.host_id = host.id
        self._host = host
        self._ssh = ssh_binary
        self._files = SftpFiles(host.id, self._start_sftp)
        self._channels = ChannelPool(self._open_channel)

    def channel_argv(self) -> list[str]:
        return [*self._options(), "-T", "--", self._host.ssh_host, "sh"]

    async def _open_channel(self) -> CommandChannel:
        return await CommandChannel.open(
            self._start_channel_process,
            timeout=self._host.connect_timeout + 5,
            on_start_failure=lambda stderr: classify_openssh(
                stderr or "connection closed", self.host_id
            ),
        )

    async def _start_channel_process(self) -> ChannelStreams:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.channel_argv(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env(),
                start_new_session=True,
                limit=STREAM_LIMIT,
            )
        except FileNotFoundError:
            raise MuxroomError(
                "ssh_error", f"{self._ssh} not found locally", host_id=self.host_id
            ) from None
        assert proc.stdin and proc.stdout and proc.stderr

        async def close() -> None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()

        async def stderr_text() -> str:
            return (await proc.stderr.read()).decode("utf-8", "replace")

        return ChannelStreams(proc.stdin, proc.stdout, proc.stderr, close, stderr_text)

    def files(self) -> SftpFiles:
        return self._files

    async def _start_sftp(self):
        proc = await self.open_sftp_process()
        try:
            client, receiver = await asyncio.wait_for(
                start_pipe_sftp_client(proc), self._host.connect_timeout + 5
            )
        except (EOFError, OSError, TimeoutError, asyncssh.Error) as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            stderr = b""
            if proc.stderr is not None:
                with contextlib.suppress(Exception):
                    stderr = await asyncio.wait_for(proc.stderr.read(), 2)
            await proc.wait()
            text = stderr.decode("utf-8", "replace")
            if text.strip():
                raise classify_openssh(text, self.host_id) from exc
            raise MuxroomError(
                "sftp_unavailable", f"SFTP failed to start: {exc!r}", host_id=self.host_id
            ) from exc

        async def closer() -> None:
            client.exit()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            receiver.cancel()

        return client, closer

    def _options(self, *, batch: bool = True) -> list[str]:
        argv = [self._ssh]
        if self._host.ssh_config:
            argv += ["-F", self._host.ssh_config]
        if batch:
            argv += ["-o", "BatchMode=yes"]
        argv += ["-o", f"ConnectTimeout={math.ceil(self._host.connect_timeout)}"]
        for option in self._host.ssh_options:
            argv += ["-o", option]
        return argv

    def command_argv(self, argv: Sequence[str], *, tty: bool = False) -> list[str]:
        return [
            *self._options(),
            "-tt" if tty else "-T",
            "--",
            self._host.ssh_host,
            remote_command(argv),
        ]

    def sftp_argv(self) -> list[str]:
        return [*self._options(), "-s", "--", self._host.ssh_host, "sftp"]

    def sign_in_argv(self) -> list[str]:
        """Interactive login that leaves a ControlMaster behind (-f -N)."""
        return [*self._options(batch=False), "-f", "-N", "--", self._host.ssh_host]

    def resolved_config_argv(self) -> list[str]:
        return [*self._options(), "-G", "--", self._host.ssh_host]

    async def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        """Run through the host's persistent `sh` (no login shell per command)."""
        try:
            return await self._channels.run(remote_command(argv), timeout=timeout)
        except ChannelBroken as exc:
            raise MuxroomError(
                "host_unreachable",
                f"Connection to {self._host.ssh_host} lost ({exc})",
                host_id=self.host_id,
            ) from exc

    async def open_terminal(
        self, argv: Sequence[str], *, cols: int, rows: int
    ) -> TerminalConnection:
        return await PtyTerminal.spawn(self.command_argv(argv, tty=True), cols=cols, rows=rows)

    async def open_sftp_process(self) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                *self.sftp_argv(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env(),
                start_new_session=True,
            )
        except FileNotFoundError:
            raise MuxroomError(
                "ssh_error", f"{self._ssh} not found", host_id=self.host_id
            ) from None

    async def resolved_config(self) -> dict[str, str]:
        result = await run_process(self.resolved_config_argv(), timeout=10)
        config: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition(" ")
            config.setdefault(key.lower(), value)
        return config

    async def close(self) -> None:
        await self._channels.close()
        await self._files.close()
