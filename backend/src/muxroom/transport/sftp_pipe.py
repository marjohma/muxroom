"""Run AsyncSSH's SFTP client over the stdio of `ssh -s <host> sftp`.

AsyncSSH only exposes SFTP on its own SSH connections. This adapter feeds
its client handler from plain asyncio pipes so the OpenSSH transport can
reuse the same SFTP implementation. It relies on AsyncSSH internals
(`SFTPClientHandler`), hence the pinned AsyncSSH version range and the
dedicated tests in tests/integration/test_ssh_transports.py.
"""

import asyncio
import contextlib

import asyncssh
from asyncssh.logging import logger as asyncssh_logger
from asyncssh.sftp import SFTPClientHandler

SFTP_VERSION = 3


class _Channel:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc

    def is_closing(self) -> bool:
        return (
            self._proc.returncode is not None
            or self._proc.stdin is None
            or (self._proc.stdin.is_closing())
        )

    async def wait_closed(self) -> None:
        await self._proc.wait()


class _Reader:
    logger = asyncssh_logger.get_child("sftp-pipe")

    def __init__(self, stream: asyncio.StreamReader) -> None:
        self._stream = stream

    async def readexactly(self, n: int) -> bytes:
        return await self._stream.readexactly(n)  # IncompleteReadError is an EOFError

    def get_extra_info(self, name: str, default: object = None) -> object:
        return default


class _Writer:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdin is not None
        self._stdin = proc.stdin
        self.channel = _Channel(proc)

    def write(self, data: bytes) -> None:
        if not self._stdin.is_closing():
            self._stdin.write(data)

    def write_eof(self) -> None:
        with contextlib.suppress(OSError, RuntimeError):
            self._stdin.write_eof()

    def close(self) -> None:
        self._stdin.close()


async def start_pipe_sftp_client(
    proc: asyncio.subprocess.Process,
) -> tuple[asyncssh.SFTPClient, asyncio.Task[None]]:
    assert proc.stdout is not None
    loop = asyncio.get_running_loop()
    handler = SFTPClientHandler(loop, "strict", _Reader(proc.stdout), _Writer(proc), SFTP_VERSION)
    await handler.start()
    receiver = loop.create_task(handler.recv_packets())
    await handler.request_limits()
    return asyncssh.SFTPClient(handler, "utf-8", "strict"), receiver
