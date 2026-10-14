import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from muxroom.api.app import create_app
from muxroom.config import AppConfig, HostConfig
from muxroom.errors import MuxroomError
from tests.fakes import FakeTmuxTransport

TOKEN = "t" * 43
ORIGIN = "http://127.0.0.1:8765"
HOST = "127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE_HEADERS = {"Origin": ORIGIN}
WS_PROTOCOLS = ["muxroom", f"muxroom.token.{TOKEN}"]
WS_HEADERS = {"Origin": ORIGIN, "Host": HOST}


@pytest.fixture
def fakes():
    return {
        "local": FakeTmuxTransport("local"),
        "down": FakeTmuxTransport("down", error=MuxroomError("host_unreachable", "no route")),
    }


@pytest.fixture
def app(fakes):
    config = AppConfig(
        hosts=[
            HostConfig(id="local", allowed_roots=["~/proj", "/data"], agents=["claude"]),
            HostConfig(id="down"),
        ]
    )
    return create_app(config, token=TOKEN, transport_factory=lambda h: fakes[h.id])


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{HOST}", headers=AUTH) as c:
        yield c


@pytest.fixture
async def anon(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{HOST}") as c:
        yield c


async def test_health_is_public(anon):
    r = await anon.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_api_requires_auth(anon):
    r = await anon.get("/api/hosts")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_token_only_accepted_as_bearer_header(anon):
    assert (
        await anon.get("/api/hosts", headers={"Authorization": "Bearer wrong"})
    ).status_code == 401
    assert (await anon.get("/api/hosts", headers={"Authorization": TOKEN})).status_code == 401
    # Cookies are sent to every port on 127.0.0.1, so they must never authenticate.
    assert (await anon.get("/api/hosts", cookies={"muxroom_session": TOKEN})).status_code == 401
    assert (await anon.get("/api/hosts", params={"token": TOKEN})).status_code == 401
    assert (await anon.get("/api/auth", params={"token": TOKEN})).status_code == 401


async def test_security_headers_on_api_responses(client):
    r = await client.get("/api/hosts")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "no-referrer"


async def test_oversized_json_body_rejected(client):
    body = '{"name": "x", "cwd": "' + "a" * (2 * 1024 * 1024) + '"}'
    r = await client.post(
        "/api/hosts/local/sessions",
        content=body,
        headers={**WRITE_HEADERS, "Content-Type": "application/json"},
    )
    assert r.status_code == 413


async def test_bad_host_header_rejected(client):
    r = await client.get("/api/hosts", headers={"Host": "evil.example:8765"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_host_header"


async def test_list_hosts_and_agents(client):
    hosts = (await client.get("/api/hosts")).json()
    assert [h["id"] for h in hosts] == ["local", "down"]
    assert hosts[0]["agents"] == ["claude"]
    agents = (await client.get("/api/agents")).json()
    assert {a["id"] for a in agents} == {"claude", "codex", "opencode"}


async def test_status_per_host_isolated(client):
    ok = (await client.get("/api/hosts/local/status")).json()
    down = (await client.get("/api/hosts/down/status")).json()
    assert ok["state"] == "ok"
    assert down["state"] == "unreachable"
    assert down["error"]["code"] == "host_unreachable" and down["error"]["hint"]


async def test_sessions_for_failing_host_return_502(client):
    r = await client.get("/api/hosts/down/sessions")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "host_unreachable"
    assert (await client.get("/api/hosts/local/sessions")).status_code == 200


async def test_unknown_host_404(client):
    r = await client.get("/api/hosts/nope/sessions")
    assert r.status_code == 404


async def test_create_session_flow(client, fakes):
    body = {"name": "work", "cwd": "~/proj", "agent": "claude"}
    r = await client.post("/api/hosts/local/sessions", json=body, headers=WRITE_HEADERS)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "work" and created["id"].startswith("$")
    assert fakes["local"].sessions[created["id"]].keys == ["claude", "Enter"]
    listed = (await client.get("/api/hosts/local/sessions")).json()["sessions"]
    assert [s["name"] for s in listed] == ["work"]
    dup = await client.post("/api/hosts/local/sessions", json=body, headers=WRITE_HEADERS)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "session_exists"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"name": "bad name", "cwd": "~/proj"}, "invalid_name"),
        ({"name": "ok", "cwd": "~/elsewhere"}, "path_not_found"),
        ({"name": "ok", "cwd": "/home/u"}, "path_not_allowed"),
        ({"name": "ok", "cwd": "~/proj", "agent": "codex"}, "agent_not_allowed"),
        ({"name": "ok"}, "invalid_request"),
        ({"name": "ok", "cwd": "~/proj", "extra": 1}, "invalid_request"),
    ],
)
async def test_create_session_errors(client, body, code):
    r = await client.post("/api/hosts/local/sessions", json=body, headers=WRITE_HEADERS)
    assert r.json()["error"]["code"] == code
    assert r.status_code in (409, 422)


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://evil.example"}])
async def test_writes_need_allowed_origin(client, headers):
    r = await client.post(
        "/api/hosts/local/sessions", json={"name": "x", "cwd": "~/proj"}, headers=headers
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden_origin"


async def test_writes_need_token(anon):
    r = await anon.post(
        "/api/hosts/local/sessions", json={"name": "x", "cwd": "~/proj"}, headers=WRITE_HEADERS
    )
    assert r.status_code == 401


# ---------------------------------------------------------------- WebSocket


def ws_client(app):
    return TestClient(app, base_url=f"http://{HOST}")


def ws_url(session_id="$0", cols=80, rows=24):
    sid = session_id.replace("$", "%24")
    return f"/api/hosts/local/sessions/{sid}/terminal?cols={cols}&rows={rows}"


def expect_close(ws, code):
    first = ws.receive_json()
    assert first["type"] == "error"
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_text()
    assert exc.value.code == code
    return first


@pytest.mark.parametrize(
    "protocols", [["muxroom"], ["muxroom", "muxroom.token.wrong"], [f"muxroom.token.{TOKEN}"]]
)
def test_ws_requires_token_subprotocol(app, fakes, protocols):
    fakes["local"].add_session("s")
    with (
        ws_client(app) as c,
        c.websocket_connect(ws_url(), headers={**WS_HEADERS}, subprotocols=protocols) as ws,
    ):
        assert expect_close(ws, 4401)["code"] == "unauthorized"


def test_ws_rejects_foreign_origin(app, fakes):
    fakes["local"].add_session("s")
    with (
        ws_client(app) as c,
        c.websocket_connect(
            ws_url(), headers={**WS_HEADERS, "Origin": "http://evil.example"}
        ) as ws,
    ):
        expect_close(ws, 4403)
    assert fakes["local"].terminals == []


def test_ws_unknown_session(app):
    with (
        ws_client(app) as c,
        c.websocket_connect(ws_url("$9"), headers={**WS_HEADERS}, subprotocols=WS_PROTOCOLS) as ws,
    ):
        assert expect_close(ws, 4404)["code"] == "session_not_found"


def test_ws_echo_resize_and_disconnect_detaches(app, fakes):
    fake = fakes["local"]
    session = fake.add_session("s")
    with ws_client(app) as c:
        with c.websocket_connect(
            ws_url(session.id, 100, 30), headers={**WS_HEADERS}, subprotocols=WS_PROTOCOLS
        ) as ws:
            assert ws.accepted_subprotocol == "muxroom"
            assert ws.receive_json() == {"type": "attached", "session_id": session.id}
            ws.send_bytes(b"ls\r")
            assert ws.receive_bytes() == b"ls\r"
            ws.send_text('{"type":"resize","cols":120,"rows":40}')
            ws.send_text('{"type":"resize","cols":0,"rows":40}')
            assert ws.receive_json()["code"] == "invalid_request"
            ws.send_bytes(b"x" * (64 * 1024 + 1))
            assert ws.receive_json()["code"] == "invalid_request"
        argv, terminal = fake.terminals[0]
        assert argv[-3:] == ["attach-session", "-t", session.id]
        assert "-d" not in argv
        assert terminal.sizes == [(100, 30), (120, 40)]
        # the bridge notices the disconnect asynchronously
        import time

        for _ in range(100):
            if terminal.close_calls:
                break
            time.sleep(0.01)
        assert terminal.close_calls == 1
    assert session.id in fake.sessions


def test_ws_exit_reason_when_session_ends(app, fakes):
    fake = fakes["local"]
    session = fake.add_session("s")
    with (
        ws_client(app) as c,
        c.websocket_connect(
            ws_url(session.id), headers={**WS_HEADERS}, subprotocols=WS_PROTOCOLS
        ) as ws,
    ):
        ws.receive_json()
        del fake.sessions[session.id]
        fake.terminals[0][1].output.put_nowait(b"")
        assert ws.receive_json() == {"type": "exit", "reason": "session_ended"}


async def test_shutdown_detaches_without_touching_sessions(app, fakes):
    fake = fakes["local"]
    session = fake.add_session("s")
    state = app.state.muxroom
    async with app.router.lifespan_context(app):
        terminal = await state.terminals.open("local", session.id, 80, 24)
        assert state.terminals.open_count == 1
    assert terminal.close_calls == 1
    assert state.terminals.open_count == 0
    assert fake.closed
    assert session.id in fake.sessions


# ---------------------------------------------------------------- config editing


@pytest.fixture
async def editable(tmp_path, fakes):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[[hosts]]\nid = "local"\nallowed_roots = ["~/proj", "/data"]\n')
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host lab\n")
    from muxroom.config import load_config

    config, _ = load_config(cfg)

    def factory(h):
        return fakes.get(h.id) or FakeTmuxTransport(h.id)

    app = create_app(
        config, token=TOKEN, transport_factory=factory, config_path=cfg, ssh_config_path=ssh_config
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{HOST}", headers=AUTH) as c:
        yield c, cfg


async def test_add_host_via_api(editable):
    client, cfg = editable
    aliases = (await client.get("/api/ssh/aliases")).json()
    assert aliases["aliases"] == ["lab"] and aliases["editing_allowed"] is True
    body = {"id": "lab", "ssh_host": "lab", "label": "Lab"}
    tested = await client.post("/api/hosts/test", json=body, headers=WRITE_HEADERS)
    assert tested.status_code == 200 and tested.json()["status"]["state"] == "ok"
    added = await client.post("/api/hosts", json=body, headers=WRITE_HEADERS)
    assert added.status_code == 201, added.text
    assert added.json()["reload"]["added"] == ["lab"]
    assert [h["id"] for h in (await client.get("/api/hosts")).json()] == ["local", "lab"]
    assert 'ssh_host = "lab"' in cfg.read_text()


async def test_add_host_rejects_ssh_options_and_needs_csrf(editable):
    client, cfg = editable
    evil = {"id": "x", "ssh_host": "lab", "ssh_options": ["ProxyCommand=touch /tmp/pwned"]}
    r = await client.post("/api/hosts", json=evil, headers=WRITE_HEADERS)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    r = await client.post(
        "/api/hosts", json={"id": "x", "ssh_host": "lab"}, headers={"Origin": "http://evil.example"}
    )
    assert r.status_code == 403
    assert "ssh_options" not in cfg.read_text()


async def test_reload_endpoint(editable):
    client, cfg = editable
    cfg.write_text(cfg.read_text() + '\n[[hosts]]\nid = "extra"\n')
    r = await client.post("/api/config/reload", headers=WRITE_HEADERS)
    assert r.status_code == 200 and r.json()["added"] == ["extra"]
    cfg.write_text("not = [valid")
    bad = await client.post("/api/config/reload", headers=WRITE_HEADERS)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_config"
    assert len((await client.get("/api/hosts")).json()) == 2


async def test_rename_session_via_api(client, fakes):
    session = fakes["local"].add_session("0")
    url = f"/api/hosts/local/sessions/{session.id.replace('$', '%24')}"
    r = await client.patch(url, json={"name": "kim-bulk"}, headers=WRITE_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "kim-bulk" and r.json()["id"] == session.id
    bad = await client.patch(url, json={"name": "x y"}, headers=WRITE_HEADERS)
    assert bad.status_code == 422
    forged = await client.patch(
        url, json={"name": "evil"}, headers={"Origin": "http://evil.example"}
    )
    assert forged.status_code == 403
    assert fakes["local"].sessions[session.id].name == "kim-bulk"


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("PATCH", "/api/hosts"),
        ("GET", "/api/does-not-exist"),
        ("DELETE", "/api/hosts/local/sessions"),
    ],
)
async def test_unknown_endpoints_explain_version_mismatch(client, method, url):
    r = await client.request(method, url, headers=WRITE_HEADERS)
    assert r.status_code == 404
    body = r.json()["error"]
    assert body["code"] == "unknown_endpoint"
    assert method in body["message"] and "restart muxroom" in body["hint"]


async def test_known_routes_still_win_over_catch_all(client):
    assert (await client.get("/api/hosts")).status_code == 200
    assert (await client.get("/api/hosts/nope/sessions")).json()["error"][
        "code"
    ] == "host_not_found"


async def test_session_list_includes_agent_activity(client, fakes):
    fake = fakes["local"]
    fake.add_session("ai")
    fake.agent_panes["$0"] = ("node /x/claude", 30)
    sessions = (await client.get("/api/hosts/local/sessions")).json()["sessions"]
    assert sessions[0]["agent"] == {"id": "claude", "label": "Claude Code", "state": "waiting"}


def test_token_file_permissions_are_enforced(tmp_path):
    import os
    import stat

    from muxroom.api.security import load_or_create_token

    state = tmp_path / "state"
    token = load_or_create_token(state)
    path = state / "token"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    os.chmod(path, 0o644)
    os.chmod(state, 0o755)
    assert load_or_create_token(state) == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert load_or_create_token(state, rotate=True) != token


def test_ws_rejects_oversized_control_frames(app, fakes):
    session = fakes["local"].add_session("s")
    with (
        ws_client(app) as c,
        c.websocket_connect(
            ws_url(session.id), headers={**WS_HEADERS}, subprotocols=WS_PROTOCOLS
        ) as ws,
    ):
        ws.receive_json()
        ws.send_text('{"type":"resize","cols":80,"rows":24,"pad":"' + "x" * 5000 + '"}')
        assert ws.receive_json()["message"] == "Frame too large"
