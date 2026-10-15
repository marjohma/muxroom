from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from muxroom.api.deps import state_of
from muxroom.errors import MuxroomError
from muxroom.files.preview import download_headers, preview_headers
from muxroom.services.files import FileInfo, MiB

router = APIRouter(prefix="/api/hosts/{host_id}/files")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileInfoOut(_Model):
    path: str
    name: str
    kind: str
    size: int
    mtime: datetime
    version: str
    preview: str | None
    outside_roots: bool = False
    download_url: str | None = None
    preview_url: str | None = None

    @classmethod
    def of(cls, info: FileInfo, links: "Links | None" = None) -> "FileInfoOut":
        readable = links is not None and info.kind == "file" and not info.outside_roots
        return cls(
            download_url=links(info.path, "attachment") if readable else None,
            preview_url=links(info.path, "inline") if readable and info.preview else None,
            path=info.path,
            name=info.name,
            kind=info.kind,
            size=info.size,
            mtime=datetime.fromtimestamp(info.mtime_ns / 1e9, tz=UTC),
            version=info.version,
            preview=info.preview,
            outside_roots=info.outside_roots,
        )


Links = Callable[[str, str], str]


def links_for(request: Request, host_id: str) -> Links:
    """Signed content URLs for <img>/<iframe>/downloads, which cannot send headers."""
    policy = state_of(request).policy

    def link(path: str, disposition: str) -> str:
        query = urlencode(policy.sign_content(host_id, path, disposition))
        return f"/api/hosts/{quote(host_id, safe='')}/files/content?{query}"

    return link


class RootOut(_Model):
    path: str
    writable: bool


class ListingOut(_Model):
    path: str
    parent: str | None
    entries: list[FileInfoOut]
    truncated: bool


class ResolveIn(_Model):
    session_id: str = Field(max_length=16)
    path: str = Field(max_length=1024)


class ResolveOut(_Model):
    cwd: str
    file: FileInfoOut


@router.get("/roots")
async def roots(host_id: str, request: Request) -> list[RootOut]:
    found = await state_of(request).files.roots(host_id)
    return [RootOut(path=r.path, writable=r.writable) for r in found]


@router.get("/list")
async def list_dir(host_id: str, path: str, request: Request) -> ListingOut:
    listing = await state_of(request).files.list(host_id, path)
    return ListingOut(
        path=listing.path,
        parent=listing.parent,
        truncated=listing.truncated,
        entries=[FileInfoOut.of(e, links_for(request, host_id)) for e in listing.entries],
    )


@router.get("/stat")
async def stat(host_id: str, path: str, request: Request) -> FileInfoOut:
    info = await state_of(request).files.stat(host_id, path)
    return FileInfoOut.of(info, links_for(request, host_id))


class CompletionItemOut(_Model):
    name: str
    kind: str
    preview: str | None
    value: str


class CompletionOut(_Model):
    cwd: str | None
    directory: str
    items: list[CompletionItemOut]
    truncated: bool


@router.get("/complete")
async def complete(
    host_id: str, request: Request, path: str = "", session_id: str | None = None
) -> CompletionOut:
    result = await state_of(request).files.complete(host_id, path, session_id=session_id)
    return CompletionOut(
        cwd=result.cwd,
        directory=result.directory,
        truncated=result.truncated,
        items=[
            CompletionItemOut(name=i.name, kind=i.kind, preview=i.preview, value=i.value)
            for i in result.items
        ],
    )


@router.post("/resolve")
async def resolve(host_id: str, body: ResolveIn, request: Request) -> ResolveOut:
    cwd, info = await state_of(request).files.resolve_relative(host_id, body.session_id, body.path)
    return ResolveOut(cwd=cwd, file=FileInfoOut.of(info, links_for(request, host_id)))


@router.get("/content")
async def content(
    host_id: str,
    path: str,
    request: Request,
    disposition: Literal["inline", "attachment"] = "attachment",
) -> Response:
    inline = disposition == "inline"
    opened = await state_of(request).files.open_read(host_id, path, inline=inline)
    if inline and opened.kind is not None:
        headers = preview_headers(opened.kind, opened.info.name)
    else:
        headers = download_headers(opened.info.name)
    headers["Content-Length"] = str(opened.info.size)
    headers["ETag"] = f'"{opened.info.version}"'
    return StreamingResponse(opened.chunks, headers=headers, media_type=headers.pop("Content-Type"))


@router.put("/content", status_code=201)
async def upload(
    host_id: str,
    dir: str,
    name: str,
    request: Request,
    overwrite: bool = False,
) -> FileInfoOut:
    state = state_of(request)
    limit = state.config.files.upload_max_mb * MiB
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise MuxroomError("file_too_large", f"Upload exceeds {limit // MiB} MiB", host_id=host_id)

    async def body() -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            if chunk:
                yield chunk

    info = await state.files.upload(host_id, dir, name, body(), overwrite=overwrite)
    return FileInfoOut.of(info, links_for(request, host_id))
