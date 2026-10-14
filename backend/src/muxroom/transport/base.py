"""Transport protocols: how muxroom reaches a host."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from muxroom.transport.files_base import FileTransport


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_status: int
    stdout: str
    stderr: str


@runtime_checkable
class TerminalConnection(Protocol):
    """A bidirectional byte stream to an interactive program on a PTY."""

    async def read(self) -> bytes:
        """Return the next chunk of output; b"" means the program exited."""
        ...

    async def write(self, data: bytes) -> None: ...

    async def resize(self, cols: int, rows: int) -> None: ...

    async def close(self) -> None:
        """Detach: close the PTY / channel. Idempotent. Never kills tmux."""
        ...


@runtime_checkable
class HostTransport(Protocol):
    host_id: str

    async def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        """Run argv to completion on the host (no shell interpretation of args)."""
        ...

    async def open_terminal(
        self, argv: Sequence[str], *, cols: int, rows: int
    ) -> TerminalConnection: ...

    def files(self) -> FileTransport:
        """File access for this host, on its own connection (independent of tmux)."""
        ...

    async def close(self) -> None:
        """Release connections. Never touches tmux sessions."""
        ...
