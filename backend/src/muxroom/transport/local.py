"""Transport for the machine muxroom runs on."""

from collections.abc import Sequence

from muxroom.transport.base import CommandResult, TerminalConnection
from muxroom.transport.local_files import LocalFiles
from muxroom.transport.process import PtyTerminal, run_process


class LocalTransport:
    def __init__(self, host_id: str) -> None:
        self.host_id = host_id
        self._files = LocalFiles()

    def files(self) -> LocalFiles:
        return self._files

    async def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        return await run_process(argv, timeout=timeout)

    async def open_terminal(
        self, argv: Sequence[str], *, cols: int, rows: int
    ) -> TerminalConnection:
        return await PtyTerminal.spawn(argv, cols=cols, rows=rows)

    async def close(self) -> None:
        return None
