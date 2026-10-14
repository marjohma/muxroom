"""Reload the configuration at runtime and add hosts from the UI.

The TOML file stays the single source of truth: hosts added in the UI are
appended to it (comments and formatting preserved), the result is validated
before anything is written, and the previous version is kept as `.bak`.
"""

import asyncio
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit
from tomlkit.items import AoT, Table

from muxroom.config import AppConfig, ConfigError, HostConfig, HostFiles, parse_config
from muxroom.errors import MuxroomError
from muxroom.services.files import FileService
from muxroom.services.hosts import HostService, HostStatus, TransportFactory
from muxroom.ssh_aliases import list_ssh_aliases
from muxroom.transport.base import CommandResult

TMUX_PATH_RE = re.compile(r"^(tmux|/[A-Za-z0-9._+/-]{1,255})$")
CONNECTABLE_STATES = {"ok", "auth_failed"}  # auth_failed: save, then use Sign in
# Common tmux locations; "$HOME/..." is expanded by the remote shell.
TMUX_CANDIDATES_SCRIPT = (
    "for p in /opt/homebrew/bin/tmux /usr/local/bin/tmux /usr/bin/tmux "
    '/home/linuxbrew/.linuxbrew/bin/tmux "$HOME/.local/bin/tmux" "$HOME/bin/tmux"; '
    'do [ -x "$p" ] && echo "$p"; done'
)


@dataclass(frozen=True, slots=True)
class HostDraft:
    """The subset of host settings that may be set from the UI.

    Deliberately excludes ssh_options / ssh_config: options such as
    ProxyCommand would let a web request run arbitrary local commands.
    """

    id: str
    ssh_host: str
    transport: str = "openssh"
    label: str | None = None
    tmux_path: str = "tmux"
    allowed_roots: list[str] = field(default_factory=lambda: ["~"])
    favorite_dirs: list[str] = field(default_factory=list)
    agents: list[str] | None = None
    upload_roots: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReloadResult:
    added: list[str]
    removed: list[str]
    changed: list[str]
    restart_required: list[str]


@dataclass(frozen=True, slots=True)
class TestResult:
    status: HostStatus
    tmux_suggestion: str | None


class ConfigManager:
    def __init__(
        self,
        path: Path,
        hosts: HostService,
        files: FileService,
        transport_factory: TransportFactory,
        on_config: Callable[[AppConfig], None],
        ssh_config_path: Path | None = None,
    ) -> None:
        self.path = path
        self.hosts = hosts
        self.files = files
        self._factory = transport_factory
        self._on_config = on_config
        self._ssh_config_path = ssh_config_path
        self._lock = asyncio.Lock()

    @property
    def config(self) -> AppConfig:
        return self.hosts.config

    # ------------------------------------------------------------------ reload

    def _read(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            return parse_config(self.path.read_text(encoding="utf-8"), str(self.path))
        except ConfigError as exc:
            raise MuxroomError("invalid_config", str(exc)) from exc

    async def reload(self) -> ReloadResult:
        async with self._lock:
            return await self._apply(self._read())

    async def _apply(self, new: AppConfig) -> ReloadResult:
        old = self.config
        restart = [
            name
            for name in ("bind", "port", "extra_origins", "state_dir")
            if getattr(old.server, name) != getattr(new.server, name)
        ]
        changes = await self.hosts.apply_config(new)
        self.files.reconfigure(new.files)
        self._on_config(new)
        return ReloadResult(
            changes.added, changes.removed, changes.changed, [f"server.{n}" for n in restart]
        )

    # ------------------------------------------------------------------ add host

    def ssh_aliases(self) -> list[str]:
        return list_ssh_aliases(self._ssh_config_path)

    def _check_editing_allowed(self) -> None:
        if not self.config.server.allow_host_editing:
            raise MuxroomError("host_editing_disabled", "Adding hosts from the UI is disabled")

    def build_host(self, draft: HostDraft) -> HostConfig:
        """Validate a draft into a HostConfig (raises MuxroomError)."""
        self._check_editing_allowed()
        if draft.transport not in ("openssh", "asyncssh"):
            raise MuxroomError("invalid_request", "Transport must be openssh or asyncssh")
        if draft.ssh_host not in self.ssh_aliases():
            raise MuxroomError("unknown_ssh_alias", f"{draft.ssh_host!r} is not in ~/.ssh/config")
        if not TMUX_PATH_RE.fullmatch(draft.tmux_path):
            raise MuxroomError("invalid_request", "tmux path must be 'tmux' or an absolute path")
        if self.config.host(draft.id) is not None:
            raise MuxroomError("host_exists", f"A host with id {draft.id!r} already exists")
        try:
            host = HostConfig(
                id=draft.id,
                label=draft.label or None,
                transport=draft.transport,  # type: ignore[arg-type]
                ssh_host=draft.ssh_host,
                tmux_path=draft.tmux_path,
                allowed_roots=draft.allowed_roots,
                favorite_dirs=draft.favorite_dirs,
                agents=draft.agents,
                files=HostFiles(upload_roots=draft.upload_roots),
            )
            AppConfig(
                server=self.config.server,
                agents=self.config.agents,
                files=self.config.files,
                hosts=[*self.config.hosts, host],
            )
        except ValueError as exc:
            raise MuxroomError("invalid_request", _first_error(exc)) from exc
        return host

    async def test_host(self, draft: HostDraft) -> TestResult:
        host = self.build_host(draft)
        probe = HostService(AppConfig(agents=self.config.agents, hosts=[host]), self._factory)
        try:
            status = await probe.status(host.id)
            suggestion = None
            if status.state == "tmux_missing":
                found: CommandResult = await probe.run_sh(host.id, TMUX_CANDIDATES_SCRIPT)
                paths = [line for line in found.stdout.splitlines() if line.startswith("/")]
                suggestion = paths[0] if paths else None
            return TestResult(status, suggestion)
        finally:
            await probe.aclose()

    async def add_host(self, draft: HostDraft) -> tuple[HostConfig, ReloadResult]:
        async with self._lock:
            host = self.build_host(draft)
            result = await self.test_host(draft)
            if result.status.state not in CONNECTABLE_STATES:
                err = result.status.error
                raise MuxroomError(
                    "connection_test_failed",
                    err.message if err else f"Host state: {result.status.state}",
                    host_id=host.id,
                    hint=err.hint if err else None,
                )
            text = self._append_host(host)
            new = parse_config(text, str(self.path))  # never write an invalid file
            self._write(text)
            return host, await self._apply(new)

    def _append_host(self, host: HostConfig) -> str:
        if self.path.exists():
            document = tomlkit.parse(self.path.read_text(encoding="utf-8"))
        else:
            document = tomlkit.document()
            document.add(tomlkit.comment("muxroom configuration (created by the UI)."))
            document.add(tomlkit.comment("See config.example.toml for all options."))
        if "hosts" not in document:
            # An absent [[hosts]] means "local only"; keep that host explicitly.
            hosts = tomlkit.aot()
            hosts.append(_host_table(AppConfig().hosts[0]))
            document["hosts"] = hosts
        aot = document["hosts"]
        if not isinstance(aot, AoT):
            raise MuxroomError("invalid_config", "'hosts' in config.toml is not an array of tables")
        aot.append(_host_table(host))
        return tomlkit.dumps(document)

    def _write(self, text: str) -> None:
        # Write through symlinks (dotfile managers) instead of replacing them.
        target = self.path.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = 0o600
        if target.exists():
            mode = target.stat().st_mode & 0o777
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        fd, temp = tempfile.mkstemp(prefix=".config-", suffix=".toml", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, mode)
            os.replace(temp, target)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


def _host_table(host: HostConfig) -> Table:
    table = tomlkit.table()
    table["id"] = host.id
    if host.label:
        table["label"] = host.label
    table["transport"] = host.transport
    if host.ssh_host:
        table["ssh_host"] = host.ssh_host
    if host.tmux_path != "tmux":
        table["tmux_path"] = host.tmux_path
    table["allowed_roots"] = list(host.allowed_roots)
    if host.favorite_dirs:
        table["favorite_dirs"] = list(host.favorite_dirs)
    if host.agents is not None:
        table["agents"] = list(host.agents)
    if host.files.upload_roots:
        files = tomlkit.table()
        files["upload_roots"] = list(host.files.upload_roots)
        table["files"] = files
    return table


def _first_error(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        first = errors()[0]
        location = ".".join(str(p) for p in first.get("loc", ()))
        message = str(first.get("msg", exc)).removeprefix("Value error, ")
        return f"{location}: {message}" if location else message
    return str(exc)
