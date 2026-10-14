from datetime import UTC, datetime

from fastapi import APIRouter, Request

from muxroom.api.deps import state_of
from muxroom.api.schemas import (
    AgentActivityOut,
    CreateSessionIn,
    RenameSessionIn,
    Session,
    SessionList,
)
from muxroom.services.sessions import CreateSession

router = APIRouter(prefix="/api/hosts/{host_id}/sessions")


@router.get("")
async def list_sessions(host_id: str, request: Request) -> SessionList:
    state = state_of(request)
    agents = state.config.agents
    rows = []
    for row in await state.sessions.shared_overview(host_id):
        activity = None
        if row.agent is not None:
            label = agents[row.agent.agent_id].label
            activity = AgentActivityOut(id=row.agent.agent_id, label=label, state=row.agent.state)
        rows.append(Session.of(row.session, activity))
    return SessionList(sessions=rows, fetched_at=datetime.now(UTC))


@router.post("", status_code=201)
async def create_session(host_id: str, body: CreateSessionIn, request: Request) -> Session:
    created = await state_of(request).sessions.create(
        host_id, CreateSession(name=body.name, cwd=body.cwd, agent=body.agent)
    )
    return Session.of(created)


@router.patch("/{session_id}")
async def rename_session(
    host_id: str, session_id: str, body: RenameSessionIn, request: Request
) -> Session:
    renamed = await state_of(request).sessions.rename(host_id, session_id, body.name)
    return Session.of(renamed)
