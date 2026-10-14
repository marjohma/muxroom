"""FileTransport over an AsyncSSH SFTP client (AsyncSSH or OpenSSH pipe)."""

import asyncio
import contextlib
import posixpath
import secrets
import stat as stat_module
from collections.abc import AsyncIterator, Awaitable, Callable

import asyncssh

from muxroom.errors import MuxroomError
from muxroom.transport.files_base import DirEntry, FileKind, FileStat
from muxroom.validation import UPLOAD_TEMP_PREFIX

Closer = Callable[[], Awaitable[None]]
ClientFactory = Callable[[], Awaitable[tuple[asyncssh.SFTPClient, Closer]]]

_CONNECTION_ERRORS = (
    asyncssh.SFTPConnectionLost,
    asyncssh.SFTPNoConnection,
    ConnectionError,
    EOFError,
    BrokenPipeError,
)


def _kind(attrs: asyncssh.SFTPAttrs) -> FileKind:
    mode = attrs.permissions or 0
    if attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY or stat_module.S_ISDIR(mode):
        return "dir"
    if attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK or stat_module.S_ISLNK(mode):
        return "symlink"
    if attrs.type == asyncssh.FILEXFER_TYPE_REGULAR or stat_module.S_ISREG(mode):
        return "file"
    return "other"


def _to_stat(path: str, attrs: asyncssh.SFTPAttrs) -> FileStat:
    mtime_ns = attrs.mtime_ns if attrs.mtime_ns is not None else (attrs.mtime or 0) * 10**9
    return FileStat(path=path, kind=_kind(attrs), size=attrs.size or 0, mtime_ns=mtime_ns)


def map_sftp_error(exc: BaseException, path: str, host_id: str) -> MuxroomError:
    if isinstance(exc, MuxroomError):
        return exc
    if isinstance(exc, asyncssh.SFTPNoSuchFile | asyncssh.SFTPNoSuchPath):
        return MuxroomError("file_not_found", f"Not found: {path}", host_id=host_id)
    if isinstance(exc, asyncssh.SFTPPermissionDenied):
        return MuxroomError("permission_denied", f"Permission denied: {path}", host_id=host_id)
    if isinstance(exc, asyncssh.SFTPFileAlreadyExists):
        return MuxroomError("file_exists", f"Already exists: {path}", host_id=host_id)
    if isinstance(exc, _CONNECTION_ERRORS):
        return MuxroomError("host_unreachable", f"SFTP connection lost ({exc})", host_id=host_id)
    if isinstance(exc, asyncssh.SFTPFailure) and "exist" in str(exc).lower():
        return MuxroomError("file_exists", f"Already exists: {path}", host_id=host_id)
    if isinstance(exc, asyncssh.SFTPError):
        return MuxroomError("invalid_request", f"{exc.reason or exc}: {path}", host_id=host_id)
    return MuxroomError("sftp_unavailable", str(exc) or type(exc).__name__, host_id=host_id)


class SftpFiles:
    """Lazily (re)connects; idempotent operations retry once after a drop."""

    def __init__(self, host_id: str, factory: ClientFactory) -> None:
        self.host_id = host_id
        self._factory = factory
        self._client: asyncssh.SFTPClient | None = None
        self._closer: Closer | None = None
        self._lock = asyncio.Lock()
        self._home: str | None = None

    async def _get(self) -> asyncssh.SFTPClient:
        async with self._lock:
            if self._client is None:
                self._client, self._closer = await self._factory()
            return self._client

    async def _reset(self) -> None:
        closer, self._client, self._closer = self._closer, None, None
        if closer:
            with contextlib.suppress(Exception):
                await closer()

    async def _idempotent(self, path: str, op):
        for attempt in (1, 2):
            client = await self._get()
            try:
                return await op(client)
            except _CONNECTION_ERRORS as exc:
                await self._reset()
                if attempt == 2:
                    raise map_sftp_error(exc, path, self.host_id) from exc
            except (asyncssh.SFTPError, OSError) as exc:
                raise map_sftp_error(exc, path, self.host_id) from exc
        raise AssertionError("unreachable")

    async def home(self) -> str:
        if self._home is None:
            self._home = await self._idempotent(".", lambda c: c.realpath("."))
        return self._home

    async def realpath(self, path: str) -> str:
        resolved = await self._idempotent(path, lambda c: c.realpath(path))
        await self.stat(resolved)  # some servers resolve non-existent paths
        return posixpath.normpath(resolved)

    async def stat(self, path: str) -> FileStat:
        return _to_stat(path, await self._idempotent(path, lambda c: c.stat(path)))

    async def lstat(self, path: str) -> FileStat:
        return _to_stat(path, await self._idempotent(path, lambda c: c.lstat(path)))

    async def list_dir(self, path: str, *, limit: int) -> tuple[list[DirEntry], bool]:
        async def scan(client: asyncssh.SFTPClient) -> tuple[list[DirEntry], bool]:
            entries: list[DirEntry] = []
            async for name in client.scandir(path):
                filename = str(name.filename)
                if filename in (".", ".."):
                    continue
                if len(entries) >= limit:
                    return entries, True
                full = posixpath.join(path, filename)
                attrs = name.attrs
                if _kind(attrs) == "other" and attrs.permissions is None:
                    attrs = await client.lstat(full)
                entries.append(DirEntry(filename, _to_stat(full, attrs)))
            return entries, False

        return await self._idempotent(path, scan)

    async def read_chunks(
        self, path: str, *, expected: FileStat, chunk_size: int, max_bytes: int
    ) -> AsyncIterator[bytes]:
        client = await self._get()
        try:
            handle = await client.open(path, "rb", block_size=chunk_size)
        except (asyncssh.SFTPError, *_CONNECTION_ERRORS) as exc:
            if isinstance(exc, _CONNECTION_ERRORS):
                await self._reset()
            raise map_sftp_error(exc, path, self.host_id) from exc
        try:
            first = _to_stat(path, await handle.stat())
            if (first.kind, first.size, first.mtime_ns) != (
                "file",
                expected.size,
                expected.mtime_ns,
            ):
                raise MuxroomError(
                    "file_changed", f"File changed before reading: {path}", host_id=self.host_id
                )
            total = 0
            while True:
                chunk = await handle.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected.size or total > max_bytes:
                    raise MuxroomError(
                        "file_changed", f"File grew while reading: {path}", host_id=self.host_id
                    )
                yield chunk
            last = _to_stat(path, await handle.stat())
            if last.mtime_ns != expected.mtime_ns:
                raise MuxroomError(
                    "file_changed", f"File changed while reading: {path}", host_id=self.host_id
                )
        except _CONNECTION_ERRORS as exc:
            await self._reset()
            raise map_sftp_error(exc, path, self.host_id) from exc
        finally:
            with contextlib.suppress(Exception):
                await handle.close()

    async def write_atomic(
        self,
        directory: str,
        name: str,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int,
        overwrite: bool,
    ) -> FileStat:
        client = await self._get()
        final = posixpath.join(directory, name)
        temp = posixpath.join(directory, f"{UPLOAD_TEMP_PREFIX}{secrets.token_hex(8)}")
        pflags = asyncssh.FXF_WRITE | asyncssh.FXF_CREAT | asyncssh.FXF_EXCL
        handle = await self._sftp(final, client.open(temp, pflags, encoding=None))
        try:
            try:
                total = 0
                async for chunk in chunks:  # errors from the client body propagate unchanged
                    total += len(chunk)
                    if total > max_bytes:
                        raise MuxroomError(
                            "file_too_large",
                            f"Upload exceeds {max_bytes} bytes",
                            host_id=self.host_id,
                        )
                    await self._sftp(final, handle.write(chunk))
            finally:
                with contextlib.suppress(Exception):
                    await handle.close()
            if overwrite:
                await self._sftp(final, client.posix_rename(temp, final))
            else:
                if await self._exists(client, final):
                    raise MuxroomError(
                        "file_exists", f"Already exists: {final}", host_id=self.host_id
                    )
                await self._sftp(final, client.rename(temp, final))  # v3: never overwrites
        except BaseException:
            with contextlib.suppress(Exception):
                await client.remove(temp)
            raise
        return await self.stat(final)

    async def _sftp(self, path: str, operation):
        try:
            return await operation
        except (asyncssh.SFTPError, *_CONNECTION_ERRORS) as exc:
            if isinstance(exc, _CONNECTION_ERRORS):
                await self._reset()
            raise map_sftp_error(exc, path, self.host_id) from exc

    @staticmethod
    async def _exists(client: asyncssh.SFTPClient, path: str) -> bool:
        try:
            await client.lstat(path)
        except asyncssh.SFTPNoSuchFile:
            return False
        return True

    async def close(self) -> None:
        await self._reset()
