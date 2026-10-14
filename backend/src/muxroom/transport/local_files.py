"""FileTransport for the local machine (blocking os calls in worker threads)."""

import asyncio
import contextlib
import errno
import os
import secrets
import stat as stat_module
from collections.abc import AsyncIterator

from muxroom.errors import MuxroomError
from muxroom.transport.files_base import DirEntry, FileKind, FileStat
from muxroom.validation import UPLOAD_TEMP_PREFIX


def _kind(mode: int) -> FileKind:
    if stat_module.S_ISREG(mode):
        return "file"
    if stat_module.S_ISDIR(mode):
        return "dir"
    if stat_module.S_ISLNK(mode):
        return "symlink"
    return "other"


def _to_stat(path: str, st: os.stat_result) -> FileStat:
    return FileStat(path=path, kind=_kind(st.st_mode), size=st.st_size, mtime_ns=st.st_mtime_ns)


def map_os_error(exc: OSError, path: str) -> MuxroomError:
    if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
        return MuxroomError("file_not_found", f"Not found: {path}")
    if exc.errno in (errno.EACCES, errno.EPERM):
        return MuxroomError("permission_denied", f"Permission denied: {path}")
    if exc.errno == errno.EEXIST:
        return MuxroomError("file_exists", f"Already exists: {path}")
    return MuxroomError("invalid_request", f"{exc.strerror or exc}: {path}")


class LocalFiles:
    def __init__(self, home: str | None = None) -> None:
        self._home = home or os.path.expanduser("~")

    async def _call(self, path: str, fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except OSError as exc:
            raise map_os_error(exc, path) from exc

    async def home(self) -> str:
        return await asyncio.to_thread(os.path.realpath, self._home)

    async def realpath(self, path: str) -> str:
        return await self._call(path, lambda: os.path.realpath(path, strict=True))

    async def stat(self, path: str) -> FileStat:
        return _to_stat(path, await self._call(path, os.stat, path))

    async def lstat(self, path: str) -> FileStat:
        return _to_stat(path, await self._call(path, os.lstat, path))

    async def list_dir(self, path: str, *, limit: int) -> tuple[list[DirEntry], bool]:
        def scan() -> tuple[list[DirEntry], bool]:
            entries: list[DirEntry] = []
            with os.scandir(path) as it:
                for entry in it:
                    if len(entries) >= limit:
                        return entries, True
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    full = os.path.join(path, entry.name)
                    entries.append(DirEntry(entry.name, _to_stat(full, st)))
            return entries, False

        return await self._call(path, scan)

    async def read_chunks(
        self, path: str, *, expected: FileStat, chunk_size: int, max_bytes: int
    ) -> AsyncIterator[bytes]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = await self._call(path, os.open, path, flags)
        try:
            st = os.fstat(fd)
            if (_kind(st.st_mode), st.st_size, st.st_mtime_ns) != (
                "file",
                expected.size,
                expected.mtime_ns,
            ):
                raise MuxroomError("file_changed", f"File changed before reading: {path}")
            total = 0
            while True:
                chunk = await asyncio.to_thread(os.read, fd, chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected.size or total > max_bytes:
                    raise MuxroomError("file_changed", f"File grew while reading: {path}")
                yield chunk
            if os.fstat(fd).st_mtime_ns != expected.mtime_ns:
                raise MuxroomError("file_changed", f"File changed while reading: {path}")
        finally:
            os.close(fd)

    async def write_atomic(
        self,
        directory: str,
        name: str,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int,
        overwrite: bool,
    ) -> FileStat:
        final = os.path.join(directory, name)
        temp = os.path.join(directory, f"{UPLOAD_TEMP_PREFIX}{secrets.token_hex(8)}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = await self._call(directory, os.open, temp, flags, 0o644)
        try:
            total = 0
            try:
                async for chunk in chunks:
                    total += len(chunk)
                    if total > max_bytes:
                        raise MuxroomError("file_too_large", f"Upload exceeds {max_bytes} bytes")
                    view = memoryview(chunk)
                    while view:
                        written = await asyncio.to_thread(os.write, fd, view)
                        view = view[written:]
                await asyncio.to_thread(os.fsync, fd)
            finally:
                os.close(fd)
            if overwrite:
                await self._call(final, os.replace, temp, final)
            else:
                await self._call(final, os.link, temp, final)  # fails if `final` exists
                os.unlink(temp)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise
        return await self.stat(final)

    async def close(self) -> None:
        return None
