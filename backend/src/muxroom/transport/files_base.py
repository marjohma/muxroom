"""File-access protocol used by the file explorer and previews."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

FileKind = Literal["file", "dir", "symlink", "other"]


@dataclass(frozen=True, slots=True)
class FileStat:
    path: str
    kind: FileKind
    size: int
    mtime_ns: int

    @property
    def version(self) -> str:
        return f"{self.mtime_ns}-{self.size}"


@dataclass(frozen=True, slots=True)
class DirEntry:
    name: str
    stat: FileStat  # lstat of the entry itself


class FileTransport(Protocol):
    """All paths are absolute POSIX paths on the target host."""

    async def home(self) -> str: ...

    async def realpath(self, path: str) -> str:
        """Resolve all symlinks. Raises file_not_found if the path does not exist."""
        ...

    async def stat(self, path: str) -> FileStat: ...

    async def lstat(self, path: str) -> FileStat: ...

    async def list_dir(self, path: str, *, limit: int) -> tuple[list[DirEntry], bool]: ...

    def read_chunks(
        self, path: str, *, expected: FileStat, chunk_size: int, max_bytes: int
    ) -> AsyncIterator[bytes]:
        """Stream a file; raises file_changed if it no longer matches `expected`."""
        ...

    async def write_atomic(
        self,
        directory: str,
        name: str,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int,
        overwrite: bool,
    ) -> FileStat: ...

    async def close(self) -> None: ...
