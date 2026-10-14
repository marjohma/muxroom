from fastapi import APIRouter, Request

from muxroom.api.deps import state_of
from muxroom.api.schemas import Agent, Host, HostStatusOut
from muxroom.config import AppConfig, HostConfig

router = APIRouter(prefix="/api")


@router.get("/agents")
async def list_agents(request: Request) -> list[Agent]:
    config = state_of(request).config
    return [Agent(id=k, label=v.label) for k, v in config.agents.items()]


def host_out(config: AppConfig, h: HostConfig) -> Host:
    return Host(
        id=h.id,
        label=h.display_label,
        transport=h.transport,
        agents=config.host_agents(h),
        favorite_dirs=h.favorite_dirs,
        allowed_roots=h.allowed_roots,
        can_sign_in=h.transport == "openssh",
    )


@router.get("/hosts")
async def list_hosts(request: Request) -> list[Host]:
    config = state_of(request).config
    return [host_out(config, h) for h in config.hosts]


@router.get("/hosts/{host_id}/status")
async def host_status(host_id: str, request: Request) -> HostStatusOut:
    return HostStatusOut.of(await state_of(request).hosts.status(host_id))
