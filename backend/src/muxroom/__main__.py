"""Command-line entry point: `muxroom [--config PATH] [--port N] ...`."""

import argparse
import ipaddress
import logging
import os
import sys
import webbrowser
from pathlib import Path

import uvicorn

from muxroom.api.app import create_app
from muxroom.api.security import load_or_create_token
from muxroom.config import ConfigError, default_config_path, load_config

log = logging.getLogger("muxroom")

# Standard asyncio, never uvloop: on Linux, uvloop starts child processes with
# almost every signal blocked. tmux clients then hang when starting a server
# and would miss SIGWINCH (resize) and SIGHUP (detach on tab close).
EVENT_LOOP = "asyncio"
WS_MAX_BYTES = 128 * 1024  # terminal input frames are at most 64 KiB


def default_static_dir() -> Path | None:
    if env := os.environ.get("MUXROOM_STATIC_DIR"):
        return Path(env)
    candidate = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


def is_loopback(bind: str) -> bool:
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="muxroom", description=__doc__)
    parser.add_argument("--config", type=Path, help="path to config.toml")
    parser.add_argument("--port", type=int, help="override server.port")
    parser.add_argument("--bind", help="override server.bind")
    parser.add_argument(
        "--allow-remote-bind",
        action="store_true",
        help="permit a non-loopback bind address (not recommended)",
    )
    parser.add_argument("--rotate-token", action="store_true", help="issue a new access token")
    parser.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    parser.add_argument("--dev", action="store_true", help="allow the Vite dev server origin")
    parser.add_argument("--reload", action="store_true", help="auto-reload (development)")
    parser.add_argument("--log-level", default="info")
    return parser.parse_args(argv)


def build_app():
    """uvicorn factory used by --reload (re-reads settings from the environment)."""
    args = parse_args(os.environ.get("MUXROOM_ARGS", "").split() or [])
    config, path = load_config(args.config)
    config = _apply_overrides(config, args)
    token = load_or_create_token(Path(config.server.state_dir).expanduser())
    return create_app(
        config,
        token=token,
        dev=args.dev,
        static_dir=default_static_dir(),
        config_path=path or args.config or default_config_path(),
    )


def _apply_overrides(config, args):
    server = config.server.model_copy(
        update={k: v for k, v in {"port": args.port, "bind": args.bind}.items() if v is not None}
    )
    return config.model_copy(update={"server": server})


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    try:
        config, path = load_config(args.config)
    except ConfigError as exc:
        sys.exit(f"muxroom: {exc}")
    config = _apply_overrides(config, args)
    if not is_loopback(config.server.bind) and not args.allow_remote_bind:
        sys.exit(f"muxroom: refusing to bind {config.server.bind!r} without --allow-remote-bind")

    token = load_or_create_token(
        Path(config.server.state_dir).expanduser(), rotate=args.rotate_token
    )
    origin_port = 5173 if args.dev else config.server.port
    print(f"muxroom config: {path or 'built-in defaults (local host only)'}", flush=True)
    # The token travels in the URL fragment: never sent to the server or logged.
    print(f"Open: http://127.0.0.1:{origin_port}/#token={token}", flush=True)
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{origin_port}/#token={token}")

    if args.reload:
        os.environ["MUXROOM_ARGS"] = " ".join(argv if argv is not None else sys.argv[1:])
        uvicorn.run(
            "muxroom.__main__:build_app",
            factory=True,
            host=config.server.bind,
            port=config.server.port,
            reload=True,
            log_level=args.log_level,
            reload_dirs=[str(Path(__file__).parent)],
            loop=EVENT_LOOP,
            ws_max_size=WS_MAX_BYTES,
        )
        return
    app = create_app(
        config,
        token=token,
        dev=args.dev,
        static_dir=default_static_dir(),
        config_path=path or args.config or default_config_path(),
    )
    uvicorn.run(
        app,
        host=config.server.bind,
        port=config.server.port,
        log_level=args.log_level,
        timeout_graceful_shutdown=3,
        loop=EVENT_LOOP,
        ws_max_size=WS_MAX_BYTES,
    )


if __name__ == "__main__":
    main()
