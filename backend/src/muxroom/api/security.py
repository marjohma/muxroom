"""Access control: bearer token, Host allowlist, Origin checks, signed content URLs.

The token is never stored in a cookie: browsers send cookies to every port
on 127.0.0.1, so any other local server could collect it. The UI keeps the
token in per-origin sessionStorage and sends it as `Authorization: Bearer`.
WebSockets carry it as a subprotocol (`muxroom.token.<token>`). Resources
loaded by <img>/<iframe>/downloads use short-lived HMAC-signed URLs bound to
one host, path and disposition.
"""

import hashlib
import hmac
import logging
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from starlette.requests import HTTPConnection

from muxroom.errors import MuxroomError

log = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TOKEN_FILE = "token"  # noqa: S105 - file name, not a secret
WS_PROTOCOL = "muxroom"
WS_TOKEN_PREFIX = "muxroom.token."  # noqa: S105 - protocol label, not a secret
TICKET_SECONDS = 3600


def load_or_create_token(state_dir: Path, *, rotate: bool = False) -> str:
    """Persist the token (mode 0600) so browser tabs survive restarts."""
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(state_dir.stat().st_mode) & 0o077:
        os.chmod(state_dir, 0o700)
    path = state_dir / TOKEN_FILE
    if path.exists() and not rotate:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            log.warning("%s was readable by other users; restricting to 0600", path)
            os.chmod(path, 0o600)
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    os.chmod(path, 0o600)
    return token


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    token: str
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str]

    @classmethod
    def build(
        cls, token: str, *, port: int, bind: str, extra_origins: list[str], dev: bool
    ) -> "AccessPolicy":
        names = {"127.0.0.1", "localhost", "[::1]"}
        if bind not in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::"):  # noqa: S104
            names.add(bind)
        ports = [port] + ([5173] if dev else [])
        hosts = {f"{name}:{p}" for name in names for p in ports}
        origins = {f"http://{h}" for h in hosts}
        for origin in extra_origins:
            origins.add(origin.rstrip("/"))
            hosts.add(origin.split("://", 1)[-1].rstrip("/"))
        return cls(token, frozenset(hosts), frozenset(origins))

    def token_matches(self, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate.encode(), self.token.encode())

    # ------------------------------------------------------------------ checks

    def check_host(self, conn: HTTPConnection) -> None:
        host = conn.headers.get("host", "")
        if host not in self.allowed_hosts:
            raise MuxroomError("bad_host_header", f"Host header not allowed: {host!r}")

    def check_origin(self, conn: HTTPConnection, *, required: bool) -> None:
        origin = conn.headers.get("origin")
        if origin is None:
            if required:
                raise MuxroomError("forbidden_origin", "Missing Origin header")
            return
        if origin not in self.allowed_origins:
            raise MuxroomError("forbidden_origin", f"Origin not allowed: {origin!r}")

    def bearer(self, conn: HTTPConnection) -> str | None:
        scheme, _, value = conn.headers.get("authorization", "").partition(" ")
        return value.strip() if scheme.lower() == "bearer" else None

    def check_http(self, conn: HTTPConnection, method: str) -> None:
        self.check_host(conn)
        # A custom Authorization header cannot be sent cross-origin without a
        # CORS preflight (never approved), so it doubles as CSRF protection.
        self.check_origin(conn, required=method not in SAFE_METHODS)
        if self.token_matches(self.bearer(conn)):
            return
        if method in SAFE_METHODS and self.ticket_valid(conn):
            return
        raise MuxroomError("unauthorized", "Not authenticated")

    def websocket_protocol(self, conn: HTTPConnection) -> str:
        """Validate a WebSocket handshake; returns the subprotocol to accept."""
        self.check_host(conn)
        self.check_origin(conn, required=True)
        offered = list(conn.scope.get("subprotocols") or []) or [
            p.strip()
            for p in conn.headers.get("sec-websocket-protocol", "").split(",")
            if p.strip()
        ]
        tokens = [p[len(WS_TOKEN_PREFIX) :] for p in offered if p.startswith(WS_TOKEN_PREFIX)]
        if WS_PROTOCOL not in offered or not any(self.token_matches(t) for t in tokens):
            raise MuxroomError("unauthorized", "Not authenticated")
        return WS_PROTOCOL

    # ------------------------------------------------------------------ signed URLs

    def _ticket_mac(self, host_id: str, path: str, disposition: str, expires: int) -> str:
        key = hmac.new(self.token.encode(), b"muxroom content ticket", hashlib.sha256).digest()
        message = f"{host_id}\n{path}\n{disposition}\n{expires}".encode()
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def sign_content(self, host_id: str, path: str, disposition: str) -> dict[str, str]:
        expires = int(time.time()) + TICKET_SECONDS
        return {
            "path": path,
            "disposition": disposition,
            "exp": str(expires),
            "sig": self._ticket_mac(host_id, path, disposition, expires),
        }

    def ticket_valid(self, conn: HTTPConnection) -> bool:
        parts = conn.url.path.split("/")
        # /api/hosts/{host_id}/files/content
        if len(parts) != 6 or parts[1:3] != ["api", "hosts"] or parts[4:] != ["files", "content"]:
            return False
        q = conn.query_params
        try:
            expires = int(q.get("exp", ""))
        except ValueError:
            return False
        if expires < time.time():
            return False
        expected = self._ticket_mac(
            parts[3], q.get("path", ""), q.get("disposition", "attachment"), expires
        )
        return hmac.compare_digest(expected, q.get("sig", ""))
