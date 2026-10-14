"""Host registry: transports, status probes and per-host cached facts."""

import asyncio
import logging
import posixpath
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from muxroom.config import AppConfig, HostConfig
from muxroom.errors import MuxroomError
from muxroom.tmux import commands as tc
from muxroom.tmux.parser import is_tmux_missing, parse_version
from muxroom.transport.base import HostTransport
from muxroom.validation import expand_home

log = logging.getLogger(__name__)

HostState = Literal["ok", "unreachable", "auth_failed", "host_key", "tmux_missing", "error"]
UNSUPPORTED_SHELLS = {"fish", "csh", "tcsh"}
ROOTS_CACHE_SECONDS = 60.0
STATUS_FROM_SUCCESS_SECONDS = 20.0

# Single line and POSIX-only so it survives any POSIX login shell. $1 = tmux path.
_PROBE_SCRIPT = (
    'printf "home=%s\\nshell=%s\\n" "$HOME" "${SHELL:-}"; '
    'if command -v "$1" >/dev/null 2>&1; then printf "tmux=%s\\n" "$("$1" -V 2>&1)"; '
    "else echo tmux_missing; fi"
)
# Canonicalize each argument with `pwd -P`; empty line if it does not exist.
_REALPATH_DIRS_SCRIPT = 'for d; do (cd -- "$d" 2>/dev/null && pwd -P) || echo; done'

_ERROR_STATE: dict[str, HostState] = {
    "host_unreachable": "unreachable",
    "timeout": "unreachable",
    "ssh_auth_failed": "auth_failed",
    "ssh_host_key_unknown": "host_key",
    "ssh_host_key_mismatch": "host_key",
    "tmux_not_found": "tmux_missing",
}


@dataclass(frozen=True, slots=True)
class HostStatus:
    host_id: str
    state: HostState
    checked_at: datetime
    tmux_version: str | None = None
    home: str | None = None
    tmux_version: str | None = None
    last_ok: tuple[float, int] | None = None  # (monotonic time, latency ms) of a recent success
    latency_ms: int | None = None
    error: MuxroomError | None = None


@dataclass(slots=True)
class HostRuntime:
    config: HostConfig
    transport: HostTransport
    tmux: tc.TmuxTarget
    home: str | None = None
    tmux_version: str | None = None
    last_ok: tuple[float, int] | None = None  # (monotonic time, latency ms) of a recent success
    roots_cache: dict[tuple[str, ...], tuple[float, list[tuple[str, str | None]]]] = field(
        default_factory=dict
    )
    probe_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


TransportFactory = Callable[[HostConfig], HostTransport]


@dataclass(frozen=True, slots=True)
class HostChanges:
    added: list[str]
    removed: list[str]
    changed: list[str]


class HostService:
    def __init__(self, config: AppConfig, transport_factory: TransportFactory) -> None:
        self.config = config
        self._factory = transport_factory
        self._hosts = {h.id: self._runtime(h) for h in config.hosts}

    def _runtime(self, host: HostConfig) -> HostRuntime:
        return HostRuntime(
            config=host,
            transport=self._factory(host),
            tmux=tc.TmuxTarget(host.tmux_path, host.tmux_socket),
        )

    async def apply_config(self, config: AppConfig) -> HostChanges:
        """Swap in a new configuration; unchanged hosts keep their connections.

        Open terminals of changed or removed hosts are separate processes /
        channels and keep running until their tabs close.
        """
        old = self._hosts
        new: dict[str, HostRuntime] = {}
        added, changed, retired = [], [], []
        for host in config.hosts:
            current = old.get(host.id)
            if current is not None and current.config == host:
                new[host.id] = current
                continue
            new[host.id] = self._runtime(host)
            (changed if current is not None else added).append(host.id)
            if current is not None:
                retired.append(current)
        removed = [host_id for host_id in old if host_id not in new]
        retired += [old[host_id] for host_id in removed]
        self._hosts = new
        self.config = config
        for rt in retired:
            try:
                await rt.transport.close()
            except Exception:
                log.exception("closing transport for %s failed", rt.config.id)
        return HostChanges(added, removed, changed)

    def ids(self) -> list[str]:
        return list(self._hosts)

    def get(self, host_id: str) -> HostRuntime:
        try:
            return self._hosts[host_id]
        except KeyError:
            raise MuxroomError("host_not_found", f"Unknown host: {host_id!r}") from None

    async def run_sh(self, host_id: str, script: str, *args: str):
        """Run a constant POSIX script with untrusted values passed as $1..$n."""
        rt = self.get(host_id)
        return await rt.transport.run(
            ["sh", "-c", script, "muxroom", *args], timeout=rt.config.command_timeout
        )

    def note_success(self, host_id: str, latency_ms: int) -> None:
        """A tmux command just succeeded: the host is reachable and tmux works."""
        self.get(host_id).last_ok = (time.monotonic(), latency_ms)

    def note_failure(self, host_id: str) -> None:
        self.get(host_id).last_ok = None

    async def status(self, host_id: str) -> HostStatus:
        rt = self.get(host_id)
        started = time.monotonic()
        now = datetime.now(UTC)
        recent = rt.last_ok
        if recent and rt.tmux_version and started - recent[0] < STATUS_FROM_SUCCESS_SECONDS:
            # Answer from the last successful session refresh: no extra remote command.
            return HostStatus(
                host_id,
                "ok",
                now,
                tmux_version=rt.tmux_version,
                home=rt.home,
                latency_ms=recent[1],
            )
        try:
            facts = await self._probe(rt)
        except MuxroomError as err:
            return HostStatus(host_id, _ERROR_STATE.get(err.code, "error"), now, error=err)
        latency = int((time.monotonic() - started) * 1000)
        shell = posixpath.basename(facts.get("shell", ""))
        if shell in UNSUPPORTED_SHELLS:
            err = MuxroomError(
                "ssh_error",
                f"Login shell {shell!r} is not supported (POSIX shells only)",
                host_id=host_id,
                hint="Use bash, zsh, sh, dash or ksh as login shell on this host.",
            )
            return HostStatus(host_id, "error", now, home=facts.get("home"), error=err)
        if "tmux_missing" in facts or "tmux" not in facts:
            err = MuxroomError(
                "tmux_not_found",
                f"tmux not found at {rt.config.tmux_path!r} on {host_id}",
                host_id=host_id,
            )
            return HostStatus(
                host_id, "tmux_missing", now, home=facts.get("home"), latency_ms=latency, error=err
            )
        version = facts["tmux"]
        rt.tmux_version = version
        rt.last_ok = (time.monotonic(), latency)
        if parse_version(version) is None:
            log.warning("unrecognized tmux version on %s: %r", host_id, version)
        return HostStatus(
            host_id, "ok", now, tmux_version=version, home=facts.get("home"), latency_ms=latency
        )

    async def _probe(self, rt: HostRuntime) -> dict[str, str]:
        result = await self.run_sh(rt.config.id, _PROBE_SCRIPT, rt.config.tmux_path)
        if result.exit_status != 0 and not result.stdout:
            if is_tmux_missing(result, "sh"):
                raise MuxroomError("ssh_error", "No POSIX sh on host", host_id=rt.config.id)
            raise MuxroomError(
                "ssh_error",
                result.stderr.strip() or f"probe failed ({result.exit_status})",
                host_id=rt.config.id,
            )
        facts: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, sep, value = line.partition("=")
            facts[key] = value if sep else ""
        if facts.get("home"):
            rt.home = facts["home"]
        return facts

    async def home(self, host_id: str) -> str:
        rt = self.get(host_id)
        async with rt.probe_lock:
            if rt.home is None:
                await self._probe(rt)
        if not rt.home:
            raise MuxroomError("ssh_error", "Could not determine $HOME", host_id=host_id)
        return rt.home

    async def expand(self, host_id: str, path: str) -> str:
        if path.startswith("~"):
            return expand_home(path, await self.home(host_id))
        return posixpath.normpath(path)

    async def realpath_dirs(self, host_id: str, paths: list[str]) -> list[str | None]:
        """Canonical paths of directories (None where missing / not a dir)."""
        if not paths:
            return []
        result = await self.run_sh(host_id, _REALPATH_DIRS_SCRIPT, *paths)
        lines = result.stdout.splitlines()
        if result.exit_status != 0 or len(lines) != len(paths):
            raise MuxroomError(
                "ssh_error", result.stderr.strip() or "directory check failed", host_id=host_id
            )
        return [line or None for line in lines]

    async def root_pairs(self, host_id: str, roots: list[str]) -> list[tuple[str, str | None]]:
        """(configured root, canonical dir or None if missing), cached briefly."""
        rt = self.get(host_id)
        key = tuple(roots)
        cached = rt.roots_cache.get(key)
        if cached and time.monotonic() - cached[0] < ROOTS_CACHE_SECONDS:
            return cached[1]
        expanded = [await self.expand(host_id, r) for r in roots]
        pairs = list(zip(roots, await self.realpath_dirs(host_id, expanded), strict=True))
        rt.roots_cache[key] = (time.monotonic(), pairs)
        return pairs

    async def canonical_roots(self, host_id: str, roots: list[str]) -> list[str]:
        return [c for _, c in await self.root_pairs(host_id, roots) if c is not None]

    async def aclose(self) -> None:
        for rt in self._hosts.values():
            try:
                await rt.transport.close()
            except Exception:
                log.exception("closing transport for %s failed", rt.config.id)
