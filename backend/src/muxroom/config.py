"""TOML configuration: hosts, agents, server and file-transfer limits."""

import os
import posixpath
import re
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from muxroom.validation import HOST_ID_RE, is_within

CONFIG_ENV_VAR = "MUXROOM_CONFIG"
SSH_HOST_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@:%-]*$")
SSH_OPTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*=\S.*$")
AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
TMUX_SOCKET_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

DEFAULT_AGENTS = {
    "claude": {"label": "Claude Code", "command": ["claude"]},
    "codex": {"label": "Codex CLI", "command": ["codex"]},
    "opencode": {"label": "OpenCode", "command": ["opencode"]},
}


class ConfigError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_root(value: str) -> str:
    if not (value == "~" or value.startswith("~/") or value.startswith("/")):
        raise ValueError(f"root must be absolute or start with '~/': {value!r}")
    if value != "/":
        value = value.rstrip("/")
    return posixpath.normpath(value) if value.startswith("/") else value


RootPath = Annotated[str, Field(min_length=1)]


class ServerConfig(_Strict):
    bind: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    extra_origins: list[str] = []
    state_dir: str = "~/.config/muxroom"
    allow_host_editing: bool = True


class AgentConfig(_Strict):
    label: str = Field(min_length=1, max_length=64)
    command: list[str] = Field(min_length=1)
    # Program names that identify this agent in `ps` (default: basename of command[0]).
    process_names: list[str] = []

    @field_validator("command")
    @classmethod
    def _non_empty_args(cls, value: list[str]) -> list[str]:
        if any(not arg or "\n" in arg or "\x00" in arg for arg in value):
            raise ValueError("agent command arguments must be non-empty single-line strings")
        return value

    @property
    def detection_names(self) -> set[str]:
        return set(self.process_names) or {posixpath.basename(self.command[0])}


class FileLimits(_Strict):
    preview_max_mb: int = Field(default=50, ge=1)
    download_max_mb: int = Field(default=2048, ge=1)
    upload_max_mb: int = Field(default=512, ge=1)
    list_max_entries: int = Field(default=5000, ge=10)


class HostFiles(_Strict):
    read_roots: list[RootPath] | None = None
    upload_roots: list[RootPath] = []

    @field_validator("read_roots", "upload_roots")
    @classmethod
    def _roots(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else [_check_root(v) for v in value]


class HostConfig(_Strict):
    id: str
    label: str | None = None
    transport: Literal["local", "openssh", "asyncssh"] = "local"
    ssh_host: str | None = None
    ssh_options: list[str] = []
    ssh_config: str | None = None
    tmux_path: str = "tmux"
    tmux_socket: str | None = None
    allowed_roots: list[RootPath] = ["~"]
    favorite_dirs: list[str] = []
    agents: list[str] | None = None
    connect_timeout: float = Field(default=10, gt=0, le=120)
    command_timeout: float = Field(default=20, gt=0, le=600)
    files: HostFiles = HostFiles()

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not HOST_ID_RE.fullmatch(value):
            raise ValueError("host id must match ^[a-z0-9][a-z0-9-]{0,31}$")
        return value

    @field_validator("ssh_host")
    @classmethod
    def _ssh_host(cls, value: str | None) -> str | None:
        if value is not None and not SSH_HOST_RE.fullmatch(value):
            raise ValueError(f"invalid ssh_host: {value!r}")
        return value

    @field_validator("ssh_options")
    @classmethod
    def _ssh_options(cls, value: list[str]) -> list[str]:
        for option in value:
            if not SSH_OPTION_RE.fullmatch(option) or "\n" in option:
                raise ValueError(f"ssh_options entries must look like 'Key=Value': {option!r}")
        return value

    @field_validator("tmux_path")
    @classmethod
    def _tmux_path(cls, value: str) -> str:
        if not value or any(c in value for c in "\n\x00") or value.startswith("-"):
            raise ValueError(f"invalid tmux_path: {value!r}")
        return value

    @field_validator("tmux_socket")
    @classmethod
    def _tmux_socket(cls, value: str | None) -> str | None:
        if value is not None and not TMUX_SOCKET_RE.fullmatch(value):
            raise ValueError(f"invalid tmux_socket: {value!r}")
        return value

    @field_validator("allowed_roots")
    @classmethod
    def _allowed_roots(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("allowed_roots must not be empty")
        return [_check_root(v) for v in value]

    @model_validator(mode="after")
    def _transport_fields(self) -> "HostConfig":
        remote = self.transport != "local"
        if remote and not self.ssh_host:
            raise ValueError(f"host {self.id!r}: ssh_host is required for {self.transport}")
        if not remote and (self.ssh_host or self.ssh_options or self.ssh_config):
            raise ValueError(
                f"host {self.id!r}: ssh_host/ssh_options/ssh_config need a remote transport"
            )
        if self.ssh_options and self.transport != "openssh":
            raise ValueError(f"host {self.id!r}: ssh_options only apply to transport 'openssh'")
        for upload_root in self.files.upload_roots:
            if not any(_root_contains(r, upload_root) for r in self.read_roots):
                raise ValueError(
                    f"host {self.id!r}: upload root {upload_root!r} is not inside a read root"
                )
        return self

    @property
    def display_label(self) -> str:
        return self.label or self.id

    @property
    def read_roots(self) -> list[str]:
        return self.files.read_roots if self.files.read_roots is not None else self.allowed_roots


def _root_contains(root: str, path: str) -> bool:
    """Containment check on unexpanded roots ('~' treated as an opaque prefix)."""
    home = "/__home__"

    def expand(p: str) -> str:
        return home + p[1:] if p.startswith("~") else p

    return is_within(posixpath.normpath(expand(path)), posixpath.normpath(expand(root)))


class AppConfig(_Strict):
    server: ServerConfig = ServerConfig()
    agents: dict[str, AgentConfig] = Field(
        default_factory=lambda: {k: AgentConfig(**v) for k, v in DEFAULT_AGENTS.items()}
    )
    files: FileLimits = FileLimits()
    hosts: list[HostConfig] = Field(default_factory=lambda: [HostConfig(id="local")])

    @field_validator("agents")
    @classmethod
    def _agent_ids(cls, value: dict[str, AgentConfig]) -> dict[str, AgentConfig]:
        for agent_id in value:
            if not AGENT_ID_RE.fullmatch(agent_id) or agent_id == "shell":
                raise ValueError(f"invalid agent id: {agent_id!r}")
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> "AppConfig":
        if not self.hosts:
            raise ValueError("at least one host must be configured")
        ids = [h.id for h in self.hosts]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate host ids: {duplicates}")
        for host in self.hosts:
            unknown = set(host.agents or []) - self.agents.keys()
            if unknown:
                raise ValueError(f"host {host.id!r} references unknown agents {sorted(unknown)}")
        return self

    def host(self, host_id: str) -> HostConfig | None:
        return next((h for h in self.hosts if h.id == host_id), None)

    def host_agents(self, host: HostConfig) -> list[str]:
        return list(self.agents) if host.agents is None else list(host.agents)


def default_config_path() -> Path:
    if env := os.environ.get(CONFIG_ENV_VAR):
        return Path(env).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "muxroom" / "config.toml"


def parse_config(text: str, source: str = "<string>") -> AppConfig:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{source}: invalid TOML: {exc}") from exc
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        lines = [f"{source}: invalid configuration:"]
        for err in exc.errors():
            location = ".".join(str(p) for p in err["loc"]) or "(root)"
            lines.append(f"  - {location}: {err['msg']}")
        raise ConfigError("\n".join(lines)) from exc


def load_config(path: Path | None = None) -> tuple[AppConfig, Path | None]:
    """Load config from `path` (or the default location).

    A missing file at the *default* location yields a local-only default
    config; an explicitly requested missing file is an error.
    """
    explicit = path is not None or CONFIG_ENV_VAR in os.environ
    path = path or default_config_path()
    if not path.exists():
        if explicit:
            raise ConfigError(f"config file not found: {path}")
        return AppConfig(), None
    return parse_config(path.read_text(encoding="utf-8"), str(path)), path
