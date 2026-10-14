"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from muxroom import __version__
from muxroom.api import routes_config, routes_files, routes_hosts, routes_sessions, ws_terminal
from muxroom.api.deps import AppState
from muxroom.api.security import AccessPolicy
from muxroom.config import AppConfig, default_config_path
from muxroom.errors import MuxroomError
from muxroom.services.config_manager import ConfigManager
from muxroom.services.files import FileService
from muxroom.services.hosts import HostService, TransportFactory
from muxroom.services.sessions import SessionService
from muxroom.services.terminals import TerminalService
from muxroom.transport.factory import create_transport

log = logging.getLogger(__name__)

PUBLIC_API_PATHS = {"/api/health"}
MAX_JSON_BODY = 1024 * 1024  # uploads (PUT .../files/content) have their own limit
UPLOAD_SUFFIX = "/files/content"


def spa_csp(host: str) -> str:
    # 'unsafe-inline' styles: xterm.js and Radix position elements via style attributes.
    return (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; "
        f"connect-src 'self' ws://{host} wss://{host}; frame-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


def error_response(err: MuxroomError) -> JSONResponse:
    return JSONResponse({"error": err.to_dict()}, status_code=err.status)


class AccessMiddleware:
    """Pure ASGI middleware (keeps streaming responses intact)."""

    def __init__(self, app: ASGIApp, policy: AccessPolicy) -> None:
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket checks happen in the endpoint so it can send a typed close code.
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = scope["path"]
        try:
            if path.startswith("/api") and path not in PUBLIC_API_PATHS:
                self.policy.check_http(request, scope["method"])
            else:
                self.policy.check_host(request)
        except MuxroomError as err:
            await error_response(err)(scope, receive, send)
            return
        if scope["method"] != "PUT" or not path.endswith(UPLOAD_SUFFIX):
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_JSON_BODY:
                err = MuxroomError("invalid_request", "Request body too large")
                await JSONResponse({"error": err.to_dict()}, status_code=413)(scope, receive, send)
                return
            receive = self._limited(receive)  # chunked bodies without Content-Length
        host = request.headers.get("host", "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Content-Security-Policy", spa_csp(host))
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except BodyTooLarge:
            err = MuxroomError("invalid_request", "Request body too large")
            await JSONResponse({"error": err.to_dict()}, status_code=413)(scope, receive, send)

    @staticmethod
    def _limited(receive: Receive) -> Receive:
        received = 0

        async def wrapper() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_JSON_BODY:
                    raise BodyTooLarge
            return message

        return wrapper


class BodyTooLarge(Exception):
    pass


def create_app(
    config: AppConfig,
    *,
    token: str,
    dev: bool = False,
    transport_factory: TransportFactory = create_transport,
    static_dir: Path | None = None,
    config_path: Path | None = None,
    ssh_config_path: Path | None = None,
) -> FastAPI:
    policy = AccessPolicy.build(
        token,
        port=config.server.port,
        bind=config.server.bind,
        extra_origins=config.server.extra_origins,
        dev=dev,
    )
    hosts = HostService(config, transport_factory)
    sessions = SessionService(hosts)
    terminals = TerminalService(hosts, sessions)
    files = FileService(hosts, sessions, config.files)

    def on_config(new: AppConfig) -> None:
        state.config = new

    manager = ConfigManager(
        config_path or default_config_path(),
        hosts,
        files,
        transport_factory,
        on_config,
        ssh_config_path=ssh_config_path,
    )
    state = AppState(config, policy, hosts, sessions, terminals, files, manager)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Detach browser clients and drop connections. tmux sessions keep running.
        await terminals.detach_all()
        await hosts.aclose()

    app = FastAPI(
        title="muxroom",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if dev else None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.muxroom = state

    @app.exception_handler(MuxroomError)
    async def _muxroom_error(_request: Request, err: MuxroomError) -> JSONResponse:
        return error_response(err)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(p) for p in first.get("loc", ()))
        return error_response(
            MuxroomError("invalid_request", f"{location}: {first.get('msg', 'invalid')}")
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(routes_hosts.router)
    app.include_router(routes_sessions.router)
    app.include_router(routes_files.router)
    app.include_router(routes_config.router)

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def unknown_endpoint(path: str, request: Request) -> JSONResponse:
        # Registered last: only reached when no API route matches path *and* method.
        return error_response(
            MuxroomError(
                "unknown_endpoint", f"This muxroom backend has no {request.method} /api/{path}"
            )
        )

    app.include_router(ws_terminal.router)

    if static_dir and (static_dir / "index.html").is_file():
        _mount_spa(app, static_dir.resolve())

    app.add_middleware(AccessMiddleware, policy=policy)
    return app


def _mount_spa(app: FastAPI, root: Path) -> None:
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-store"})
