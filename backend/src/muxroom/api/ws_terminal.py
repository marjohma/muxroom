import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket

from muxroom.api.deps import state_of
from muxroom.api.security import WS_PROTOCOL
from muxroom.errors import MuxroomError

log = logging.getLogger(__name__)
router = APIRouter()

CLOSE_CODES = {
    "unauthorized": 4401,
    "forbidden_origin": 4403,
    "bad_host_header": 4403,
    "host_not_found": 4404,
    "session_not_found": 4404,
    "invalid_request": 4422,
}


def offered_protocol(websocket: WebSocket) -> str | None:
    offered = list(websocket.scope.get("subprotocols") or [])
    offered += [p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    return WS_PROTOCOL if WS_PROTOCOL in offered else None


async def reject(websocket: WebSocket, err: MuxroomError) -> None:
    # Echo our protocol if offered; otherwise browsers fail the handshake and
    # the client would only see a generic 1006 instead of the reason.
    await websocket.accept(subprotocol=offered_protocol(websocket))
    await websocket.send_text(json.dumps({"type": "error", **err.to_dict()}))
    await websocket.close(code=CLOSE_CODES.get(err.code, 4502), reason=err.code)


@router.websocket("/api/hosts/{host_id}/sessions/{session_id}/terminal")
async def terminal(
    websocket: WebSocket, host_id: str, session_id: str, cols: int = 80, rows: int = 24
) -> None:
    state = state_of(websocket)
    try:
        protocol = state.policy.websocket_protocol(websocket)
        terminal = await state.terminals.open(host_id, session_id, cols, rows)
    except MuxroomError as err:
        await reject(websocket, err)
        return
    try:
        await websocket.accept(subprotocol=protocol)
        await websocket.send_text(json.dumps({"type": "attached", "session_id": session_id}))
    except Exception:
        await state.terminals.release(terminal)
        raise
    log.info("terminal attached host=%s session=%s", host_id, session_id)
    try:
        await state.terminals.bridge(host_id, session_id, websocket, terminal)
    finally:
        log.info("terminal detached host=%s session=%s", host_id, session_id)
        with contextlib.suppress(Exception):  # already closed by the peer
            await websocket.close()


@router.websocket("/api/hosts/{host_id}/signin")
async def sign_in(websocket: WebSocket, host_id: str, cols: int = 80, rows: int = 24) -> None:
    state = state_of(websocket)
    try:
        protocol = state.policy.websocket_protocol(websocket)
        state.hosts.get(host_id)
    except MuxroomError as err:
        await reject(websocket, err)
        return
    await websocket.accept(subprotocol=protocol)
    try:
        await state.terminals.sign_in(host_id, websocket, cols, rows)
    except MuxroomError as err:
        await websocket.send_text(json.dumps({"type": "error", **err.to_dict()}))
        await websocket.close(code=CLOSE_CODES.get(err.code, 4502), reason=err.code)
        return
    with contextlib.suppress(Exception):  # already closed by the peer
        await websocket.close()
