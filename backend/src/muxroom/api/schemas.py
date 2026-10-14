"""Pydantic request/response models of the HTTP API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from muxroom.services.hosts import HostStatus
from muxroom.tmux.parser import TmuxSession


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(_Model):
    code: str
    message: str
    hint: str | None = None
    host_id: str | None = None


class Agent(_Model):
    id: str
    label: str


class Host(_Model):
    id: str
    label: str
    transport: Literal["local", "openssh", "asyncssh"]
    agents: list[str]
    favorite_dirs: list[str]
    allowed_roots: list[str]
    can_sign_in: bool


class HostStatusOut(_Model):
    host_id: str
    state: str
    checked_at: datetime
    tmux_version: str | None = None
    home: str | None = None
    latency_ms: int | None = None
    error: ErrorBody | None = None

    @classmethod
    def of(cls, s: HostStatus) -> "HostStatusOut":
        return cls(
            host_id=s.host_id,
            state=s.state,
            checked_at=s.checked_at,
            tmux_version=s.tmux_version,
            home=s.home,
            latency_ms=s.latency_ms,
            error=ErrorBody(**s.error.to_dict()) if s.error else None,
        )


class AgentActivityOut(_Model):
    id: str
    label: str
    state: Literal["working", "waiting"]


class Session(_Model):
    id: str
    name: str
    created: datetime
    last_activity: datetime
    windows: int
    attached_clients: int
    path: str
    agent: AgentActivityOut | None = None

    @classmethod
    def of(cls, s: TmuxSession, agent: AgentActivityOut | None = None) -> "Session":
        return cls(
            agent=agent,
            id=s.id,
            name=s.name,
            created=s.created,
            last_activity=s.last_activity,
            windows=s.windows,
            attached_clients=s.attached_clients,
            path=s.path,
        )


class SessionList(_Model):
    sessions: list[Session]
    fetched_at: datetime


class RenameSessionIn(_Model):
    name: str = Field(max_length=64)


class CreateSessionIn(_Model):
    name: str = Field(max_length=64)
    cwd: str = Field(max_length=1024)
    agent: str = Field(default="shell", max_length=32)
