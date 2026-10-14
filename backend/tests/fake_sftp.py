"""A mocked asyncssh.SFTPClient backed by a local directory tree.

Mirrors the SFTPv3 behaviour that matters here: second-resolution mtimes,
lstat-style readdir attributes, non-overwriting rename, error types.
"""

import os
import stat as st

import asyncssh


def attrs_of(result: os.stat_result) -> asyncssh.SFTPAttrs:
    mode = result.st_mode
    if st.S_ISDIR(mode):
        kind = asyncssh.FILEXFER_TYPE_DIRECTORY
    elif st.S_ISLNK(mode):
        kind = asyncssh.FILEXFER_TYPE_SYMLINK
    elif st.S_ISREG(mode):
        kind = asyncssh.FILEXFER_TYPE_REGULAR
    else:
        kind = asyncssh.FILEXFER_TYPE_SPECIAL
    return asyncssh.SFTPAttrs(
        type=kind, size=result.st_size, permissions=mode, mtime=int(result.st_mtime)
    )


def _wrap(fn, path):
    try:
        return fn()
    except FileNotFoundError:
        raise asyncssh.SFTPNoSuchFile(f"No such file: {path}") from None
    except PermissionError:
        raise asyncssh.SFTPPermissionDenied(f"Permission denied: {path}") from None
    except FileExistsError:
        raise asyncssh.SFTPFailure(f"File exists: {path}") from None


class FakeHandle:
    def __init__(self, client: "FakeSftpClient", path: str, fd: int) -> None:
        self.client, self.path, self.fd = client, path, fd
        self.reads = 0
        client.open_handles += 1

    async def stat(self):
        return attrs_of(os.fstat(self.fd))

    async def read(self, size: int = -1) -> bytes:
        if self.client.drop_after_reads is not None and self.reads >= self.client.drop_after_reads:
            raise asyncssh.SFTPConnectionLost("connection lost")
        self.reads += 1
        return os.read(self.fd, size if size > 0 else 1 << 30)

    async def write(self, data: bytes) -> int:
        return os.write(self.fd, data)

    async def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
            self.client.open_handles -= 1


class FakeSftpClient:
    def __init__(self, home: str) -> None:
        self.home = home
        self.denied: set[str] = set()
        self.drop_after_reads: int | None = None
        self.open_handles = 0
        self.exited = False

    def _check(self, path: str) -> None:
        if any(path == d or path.startswith(d + "/") for d in self.denied):
            raise asyncssh.SFTPPermissionDenied(f"Permission denied: {path}")

    async def realpath(self, path: str) -> str:
        if path == ".":
            return self.home
        return os.path.realpath(path)  # non-strict, like many servers

    async def stat(self, path: str):
        self._check(path)
        return attrs_of(_wrap(lambda: os.stat(path), path))

    async def lstat(self, path: str):
        self._check(path)
        return attrs_of(_wrap(lambda: os.lstat(path), path))

    async def scandir(self, path: str):
        self._check(path)
        names = _wrap(lambda: os.listdir(path), path)
        for name in [".", "..", *names]:
            full = os.path.join(path, name)
            yield asyncssh.SFTPName(name, "", attrs_of(os.lstat(full)))

    async def open(self, path: str, pflags_or_mode="r", block_size=None, encoding="utf-8"):
        self._check(path)
        if isinstance(pflags_or_mode, int):
            flags = os.O_WRONLY | os.O_CREAT
            if pflags_or_mode & asyncssh.FXF_EXCL:
                flags |= os.O_EXCL
        else:
            flags = os.O_RDONLY
        fd = _wrap(lambda: os.open(path, flags, 0o644), path)
        return FakeHandle(self, path, fd)

    async def posix_rename(self, old: str, new: str) -> None:
        _wrap(lambda: os.replace(old, new), new)

    async def rename(self, old: str, new: str) -> None:
        if os.path.lexists(new):
            raise asyncssh.SFTPFailure(f"File exists: {new}")
        _wrap(lambda: os.rename(old, new), new)

    async def remove(self, path: str) -> None:
        _wrap(lambda: os.remove(path), path)

    def exit(self) -> None:
        self.exited = True
