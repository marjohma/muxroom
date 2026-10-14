"""Preview kinds, magic-byte checks and hardened response headers."""

import posixpath
from typing import Literal
from urllib.parse import quote

PreviewKind = Literal["pdf", "png", "jpeg", "svg"]

EXTENSIONS: dict[str, PreviewKind] = {
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".svg": "svg",
}
CONTENT_TYPES: dict[PreviewKind, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
}
MAGIC_WINDOW = 1024

# Opened directly in a tab, these render in an opaque origin without scripts.
SANDBOX_CSP = (
    "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"
)


def kind_from_name(name: str) -> PreviewKind | None:
    return EXTENSIONS.get(posixpath.splitext(name)[1].lower())


def matches_magic(kind: PreviewKind, head: bytes) -> bool:
    head = head[:MAGIC_WINDOW]
    if kind == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if kind == "jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if kind == "pdf":
        return b"%PDF-" in head
    text = head.removeprefix(b"\xef\xbb\xbf").lstrip()
    return text.startswith(b"<") and b"<svg" in head.lower()


def _disposition(kind: str, filename: str) -> str:
    fallback = "".join(c if (c.isascii() and c.isalnum()) or c in "._-" else "_" for c in filename)
    return f"{kind}; filename=\"{fallback or 'file'}\"; filename*=UTF-8''{quote(filename)}"


def preview_headers(kind: PreviewKind, filename: str) -> dict[str, str]:
    headers = {
        "Content-Type": CONTENT_TYPES[kind],
        "Content-Disposition": _disposition("inline", filename),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Frame-Options": "SAMEORIGIN",  # the PDF preview is an <iframe> in the UI
        "Content-Security-Policy": "frame-ancestors 'self'",
    }
    # Native PDF viewers may refuse to render under a sandbox CSP; PDFs rely
    # on the viewer's own isolation instead.
    if kind != "pdf":
        headers["Content-Security-Policy"] = SANDBOX_CSP
    return headers


def download_headers(filename: str) -> dict[str, str]:
    return {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": _disposition("attachment", filename),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Content-Security-Policy": SANDBOX_CSP,
    }
