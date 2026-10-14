from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from muxroom.api.deps import state_of
from muxroom.api.routes_hosts import host_out
from muxroom.api.schemas import Host, HostStatusOut
from muxroom.services.config_manager import HostDraft, ReloadResult

router = APIRouter(prefix="/api")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReloadOut(_Model):
    added: list[str]
    removed: list[str]
    changed: list[str]
    restart_required: list[str]

    @classmethod
    def of(cls, r: ReloadResult) -> "ReloadOut":
        return cls(
            added=r.added, removed=r.removed, changed=r.changed, restart_required=r.restart_required
        )


class AliasesOut(_Model):
    aliases: list[str]
    configured: list[str]
    editing_allowed: bool
    config_path: str


class HostDraftIn(_Model):
    id: str = Field(max_length=32)
    ssh_host: str = Field(max_length=255)
    transport: str = "openssh"
    label: str | None = Field(default=None, max_length=64)
    tmux_path: str = Field(default="tmux", max_length=256)
    allowed_roots: list[str] = Field(default_factory=lambda: ["~"], max_length=32)
    favorite_dirs: list[str] = Field(default_factory=list, max_length=32)
    agents: list[str] | None = Field(default=None, max_length=32)
    upload_roots: list[str] = Field(default_factory=list, max_length=32)

    def draft(self) -> HostDraft:
        return HostDraft(**self.model_dump())


class TestOut(_Model):
    status: HostStatusOut
    tmux_suggestion: str | None


class AddHostOut(_Model):
    host: Host
    reload: ReloadOut


@router.post("/config/reload")
async def reload_config(request: Request) -> ReloadOut:
    return ReloadOut.of(await state_of(request).config_manager.reload())


@router.get("/ssh/aliases")
async def ssh_aliases(request: Request) -> AliasesOut:
    state = state_of(request)
    manager = state.config_manager
    return AliasesOut(
        aliases=manager.ssh_aliases(),
        configured=[h.ssh_host for h in state.config.hosts if h.ssh_host],
        editing_allowed=state.config.server.allow_host_editing,
        config_path=str(manager.path),
    )


@router.post("/hosts/test")
async def test_host(body: HostDraftIn, request: Request) -> TestOut:
    result = await state_of(request).config_manager.test_host(body.draft())
    return TestOut(status=HostStatusOut.of(result.status), tmux_suggestion=result.tmux_suggestion)


@router.post("/hosts", status_code=201)
async def add_host(body: HostDraftIn, request: Request) -> AddHostOut:
    state = state_of(request)
    host, reload = await state.config_manager.add_host(body.draft())
    return AddHostOut(host=host_out(state.config, host), reload=ReloadOut.of(reload))
