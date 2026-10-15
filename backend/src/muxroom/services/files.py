"""File explorer operations constrained to configured canonical roots."""

import posixpath
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Literal

from muxroom.config import FileLimits
from muxroom.errors import MuxroomError
from muxroom.files.policy import describe_roots, is_contained, parent_within
from muxroom.files.preview import PreviewKind, kind_from_name, matches_magic
from muxroom.services.hosts import HostService
from muxroom.services.sessions import SessionService
from muxroom.transport.files_base import FileStat, FileTransport
from muxroom.validation import (
    CONTROL_CHARS_RE,
    expand_home,
    validate_filename,
    validate_relative_path,
    validate_user_path,
)

MiB = 1024 * 1024
CHUNK_SIZE = 256 * 1024
ROOTS_CACHE_SECONDS = 60.0
Purpose = Literal["read", "upload"]


@dataclass(frozen=True, slots=True)
class FileInfo:
    path: str
    name: str
    kind: str
    size: int
    mtime_ns: int
    version: str
    preview: PreviewKind | None
    outside_roots: bool = False

    @classmethod
    def of(
        cls,
        stat: FileStat,
        *,
        name: str | None = None,
        outside: bool = False,
        target: str | None = None,
    ) -> "FileInfo":
        name = name if name is not None else (posixpath.basename(stat.path) or stat.path)
        preview = kind_from_name(name) or (kind_from_name(target) if target else None)
        return cls(
            path=stat.path,
            name=name,
            kind=stat.kind,
            size=stat.size,
            mtime_ns=stat.mtime_ns,
            version=stat.version,
            preview=preview if stat.kind == "file" and not outside else None,
            outside_roots=outside,
        )


@dataclass(frozen=True, slots=True)
class CompletionItem:
    name: str
    kind: str
    preview: PreviewKind | None
    value: str  # full text to put into the input


@dataclass(frozen=True, slots=True)
class Completion:
    cwd: str | None
    directory: str
    items: list[CompletionItem]
    truncated: bool


@dataclass(frozen=True, slots=True)
class Root:
    path: str
    writable: bool


@dataclass(frozen=True, slots=True)
class Listing:
    path: str
    parent: str | None
    entries: list[FileInfo]
    truncated: bool


@dataclass(frozen=True, slots=True)
class OpenedFile:
    info: FileInfo
    kind: PreviewKind | None
    chunks: AsyncIterator[bytes]


async def _prepend(first: bytes, rest: AsyncGenerator[bytes]) -> AsyncIterator[bytes]:
    """Re-attach the sniffed first chunk; always closes `rest` (releases the handle)."""
    try:
        if first:
            yield first
        async for chunk in rest:
            yield chunk
    finally:
        await rest.aclose()


class FileService:
    def __init__(self, hosts: HostService, sessions: SessionService, limits: FileLimits) -> None:
        self.hosts = hosts
        self.sessions = sessions
        self.limits = limits
        self._roots_cache: dict[
            tuple[str, Purpose], tuple[float, list[tuple[str, str | None]]]
        ] = {}

    def reconfigure(self, limits: FileLimits) -> None:
        self.limits = limits
        self._roots_cache.clear()

    def _files(self, host_id: str) -> FileTransport:
        return self.hosts.get(host_id).transport.files()

    async def _root_pairs(self, host_id: str, purpose: Purpose) -> list[tuple[str, str | None]]:
        """(configured root, canonical path or None if missing), cached briefly."""
        cached = self._roots_cache.get((host_id, purpose))
        if cached and time.monotonic() - cached[0] < ROOTS_CACHE_SECONDS:
            return cached[1]
        cfg = self.hosts.get(host_id).config
        configured = cfg.read_roots if purpose == "read" else cfg.files.upload_roots
        files = self._files(host_id)
        home = await files.home() if any(r.startswith("~") for r in configured) else ""
        pairs: list[tuple[str, str | None]] = []
        for root in configured:
            try:
                pairs.append((root, await files.realpath(expand_home(root, home))))
            except MuxroomError as err:
                if err.code not in ("file_not_found", "permission_denied"):
                    raise
                pairs.append((root, None))
        self._roots_cache[(host_id, purpose)] = (time.monotonic(), pairs)
        return pairs

    async def _canonical_roots(self, host_id: str, purpose: Purpose) -> list[str]:
        return [c for _, c in await self._root_pairs(host_id, purpose) if c is not None]

    async def roots(self, host_id: str) -> list[Root]:
        read = await self._canonical_roots(host_id, "read")
        upload = await self._canonical_roots(host_id, "upload")
        return [Root(r, writable=is_contained(r, upload)) for r in read] + [
            Root(u, writable=True) for u in upload if not is_contained(u, read)
        ]

    async def resolve(self, host_id: str, path: str, purpose: Purpose = "read") -> str:
        """Validate, canonicalize and confine a client-supplied path."""
        validate_user_path(path)
        files = self._files(host_id)
        if path.startswith("~"):
            path = expand_home(path, await files.home())
        normalized = posixpath.normpath(path)
        roots = await self._canonical_roots(host_id, purpose)
        canonical = await files.realpath(normalized)
        if not is_contained(canonical, roots):
            allowed = describe_roots(await self._root_pairs(host_id, purpose))
            if purpose == "upload":
                code, what, key = "upload_not_allowed", "may upload to", "files.upload_roots"
            else:
                code, what, key = "path_outside_roots", "may read", "files.read_roots"
            shown = normalized if canonical == normalized else f"{normalized} (→ {canonical})"
            raise MuxroomError(
                code,
                f"{shown} is outside the folders muxroom {what} on this host. Allowed: {allowed}",
                host_id=host_id,
                hint=f"Edit {key} for host '{host_id}' in config.toml, then click Reload config.",
            )
        return canonical

    async def list(self, host_id: str, path: str) -> Listing:
        canonical = await self.resolve(host_id, path)
        files = self._files(host_id)
        if (await files.stat(canonical)).kind != "dir":
            raise MuxroomError("not_a_directory", f"Not a directory: {canonical}", host_id=host_id)
        roots = await self._canonical_roots(host_id, "read")
        raw, truncated = await files.list_dir(canonical, limit=self.limits.list_max_entries)
        entries: list[FileInfo] = []
        for entry in raw:
            if entry.stat.kind != "symlink":
                entries.append(FileInfo.of(entry.stat, name=entry.name))
                continue
            try:
                target_path = await files.realpath(entry.stat.path)
                target = await files.stat(target_path)
                outside = not is_contained(target_path, roots)
                info = FileInfo.of(
                    FileStat(entry.stat.path, target.kind, target.size, target.mtime_ns),
                    name=entry.name,
                    outside=outside,
                    target=target_path,
                )
            except MuxroomError:
                info = FileInfo.of(entry.stat, name=entry.name, outside=True)  # broken link
            entries.append(info)
        entries.sort(key=lambda e: (e.kind != "dir", e.name.lower()))
        return Listing(canonical, parent_within(canonical, roots), entries, truncated)

    async def stat(self, host_id: str, path: str) -> FileInfo:
        canonical = await self.resolve(host_id, path)
        return FileInfo.of(await self._files(host_id).stat(canonical))

    async def complete(
        self, host_id: str, text: str, *, session_id: str | None, limit: int = 50
    ) -> Completion:
        """Path completion for the preview pane: folders and previewable files.

        `text` is absolute, `~`-based, or relative to the session's pane
        directory. The listed folder must lie inside the read roots.
        """
        if CONTROL_CHARS_RE.search(text) or len(text.encode()) > 1024:
            raise MuxroomError("invalid_path", "Invalid path")
        dir_part, _, prefix = text.rpartition("/")
        if text == "~":
            dir_part, prefix = "~", ""
        typed_dir = dir_part + "/" if dir_part or text.startswith("/") else ""
        cwd: str | None = None
        if text.startswith(("/", "~")):
            directory = dir_part or "/"
        else:
            if session_id is None:
                raise MuxroomError("invalid_path", "Relative paths need a session")
            cwd = await self.sessions.pane_cwd(host_id, session_id)
            directory = posixpath.join(cwd, dir_part) if dir_part else cwd
        listing = await self.list(host_id, directory)
        show_hidden = prefix.startswith(".")
        matches = [
            e
            for e in listing.entries
            if e.name.lower().startswith(prefix.lower())
            and (show_hidden or not e.name.startswith("."))
            and not e.outside_roots
            and (e.kind == "dir" or e.preview is not None)
        ]
        items = [
            CompletionItem(
                name=e.name,
                kind=e.kind,
                preview=e.preview,
                value=typed_dir + e.name + ("/" if e.kind == "dir" else ""),
            )
            for e in matches[:limit]
        ]
        return Completion(
            cwd=cwd,
            directory=listing.path,
            items=items,
            truncated=len(matches) > limit or listing.truncated,
        )

    async def resolve_relative(
        self, host_id: str, session_id: str, rel: str
    ) -> tuple[str, FileInfo]:
        validate_relative_path(rel)
        cwd = await self.sessions.pane_cwd(host_id, session_id)
        return cwd, await self.stat(host_id, posixpath.join(cwd, rel))

    async def open_read(self, host_id: str, path: str, *, inline: bool) -> OpenedFile:
        canonical = await self.resolve(host_id, path)
        files = self._files(host_id)
        stat = await files.stat(canonical)
        if stat.kind != "file":
            raise MuxroomError("not_a_file", f"Not a regular file: {canonical}", host_id=host_id)
        info = FileInfo.of(stat)
        limit = (self.limits.preview_max_mb if inline else self.limits.download_max_mb) * MiB
        if stat.size > limit:
            raise MuxroomError(
                "file_too_large",
                f"{info.name} is {stat.size / MiB:.1f} MiB; the limit is {limit // MiB} MiB",
                host_id=host_id,
            )
        kind = info.preview
        if inline and kind is None:
            raise MuxroomError(
                "preview_unsupported", f"Cannot preview {info.name}", host_id=host_id
            )
        chunks = files.read_chunks(canonical, expected=stat, chunk_size=CHUNK_SIZE, max_bytes=limit)
        first = b""
        async for chunk in chunks:
            first = chunk
            break
        if inline and kind is not None and not matches_magic(kind, first):
            await chunks.aclose()
            raise MuxroomError(
                "preview_unsupported",
                f"{info.name} does not look like a {kind.upper()} file",
                host_id=host_id,
                hint="The content does not match the file extension; download it instead.",
            )
        return OpenedFile(info, kind, _prepend(first, chunks))

    async def upload(
        self,
        host_id: str,
        directory: str,
        name: str,
        body: AsyncIterator[bytes],
        *,
        overwrite: bool,
    ) -> FileInfo:
        validate_filename(name)
        canonical_dir = await self.resolve(host_id, directory, purpose="upload")
        files = self._files(host_id)
        if (await files.stat(canonical_dir)).kind != "dir":
            raise MuxroomError(
                "not_a_directory", f"Not a directory: {canonical_dir}", host_id=host_id
            )
        stat = await files.write_atomic(
            canonical_dir,
            name,
            body,
            max_bytes=self.limits.upload_max_mb * MiB,
            overwrite=overwrite,
        )
        # Defense against a directory swapped for a symlink during the upload.
        try:
            final = await files.realpath(stat.path)
        except MuxroomError:
            final = stat.path
        if not is_contained(final, await self._canonical_roots(host_id, "upload")):
            raise MuxroomError(
                "upload_not_allowed",
                f"{final} ended up outside the upload folders (directory changed during upload)",
                host_id=host_id,
            )
        return FileInfo.of(stat)
