import asyncio

import pytest

from muxroom.config import AppConfig, HostConfig
from muxroom.errors import MuxroomError
from muxroom.services.hosts import HostService
from muxroom.services.sessions import CreateSession, SessionService
from tests.fakes import FakeTmuxTransport


def make(transport: FakeTmuxTransport, **host_kwargs) -> tuple[SessionService, HostService]:
    host_kwargs.setdefault("allowed_roots", ["~/proj", "/data"])
    config = AppConfig(hosts=[HostConfig(id="local", **host_kwargs)])
    hosts = HostService(config, lambda _h: transport)
    return SessionService(hosts), hosts


async def test_list_empty_when_no_server():
    sessions, _ = make(FakeTmuxTransport())
    assert await sessions.list("local") == []


async def test_list_returns_sessions():
    fake = FakeTmuxTransport()
    fake.add_session("a")
    fake.add_session("b")
    sessions, _ = make(fake)
    assert [s.name for s in await sessions.list("local")] == ["a", "b"]


async def test_unknown_host():
    sessions, _ = make(FakeTmuxTransport())
    with pytest.raises(MuxroomError) as err:
        await sessions.list("nope")
    assert err.value.code == "host_not_found"


async def test_missing_tmux_on_list():
    sessions, _ = make(FakeTmuxTransport(tmux_installed=False))
    with pytest.raises(MuxroomError) as err:
        await sessions.list("local")
    assert err.value.code == "tmux_not_found"


async def test_create_shell_session_in_expanded_dir():
    fake = FakeTmuxTransport()
    sessions, _ = make(fake)
    session = await sessions.create("local", CreateSession("work", "~/proj"))
    assert session.name == "work"
    assert fake.sessions[session.id].cwd == "/home/u/proj"
    assert fake.sessions[session.id].keys == []


async def test_create_with_agent_sends_configured_command():
    fake = FakeTmuxTransport()
    sessions, _ = make(fake)
    session = await sessions.create("local", CreateSession("ai", "/data", agent="codex"))
    assert fake.sessions[session.id].keys == ["codex", "Enter"]


async def test_create_duplicate_name():
    fake = FakeTmuxTransport()
    fake.add_session("dup")
    sessions, _ = make(fake)
    with pytest.raises(MuxroomError) as err:
        await sessions.create("local", CreateSession("dup", "/data"))
    assert err.value.code == "session_exists"


@pytest.mark.parametrize(
    ("cwd", "code"),
    [
        ("/data/missing", "path_not_found"),
        ("/home/u", "path_not_allowed"),
        ("/home/u/proj/../../u", "path_not_allowed"),
        ("relative", "invalid_path"),
        ("/data/$(touch x)", "path_not_found"),
    ],
)
async def test_create_invalid_paths(cwd, code):
    fake = FakeTmuxTransport()
    sessions, _ = make(fake)
    with pytest.raises(MuxroomError) as err:
        await sessions.create("local", CreateSession("x", cwd))
    assert err.value.code == code
    assert fake.sessions == {}


async def test_create_rejects_symlink_escape():
    fake = FakeTmuxTransport(
        dirs=["/home/u", "/home/u/proj", "/etc"], symlinks={"/home/u/proj/link": "/etc"}
    )
    sessions, _ = make(fake)
    with pytest.raises(MuxroomError) as err:
        await sessions.create("local", CreateSession("x", "~/proj/link"))
    assert err.value.code == "path_not_allowed"


async def test_create_unknown_agent():
    sessions, _ = make(FakeTmuxTransport(), agents=["claude"])
    with pytest.raises(MuxroomError) as err:
        await sessions.create("local", CreateSession("x", "/data", agent="codex"))
    assert err.value.code == "agent_not_allowed"


async def test_create_invalid_name_runs_nothing():
    fake = FakeTmuxTransport()
    sessions, _ = make(fake)
    with pytest.raises(MuxroomError):
        await sessions.create("local", CreateSession("a;rm -rf ~", "/data"))
    assert fake.calls == []


async def test_status_ok_and_cached_home():
    fake = FakeTmuxTransport()
    _, hosts = make(fake)
    status = await hosts.status("local")
    assert status.state == "ok"
    assert status.tmux_version == "tmux 3.5a"
    assert await hosts.home("local") == "/home/u"


@pytest.mark.parametrize(
    ("code", "state"),
    [
        ("host_unreachable", "unreachable"),
        ("ssh_auth_failed", "auth_failed"),
        ("ssh_host_key_unknown", "host_key"),
        ("timeout", "unreachable"),
    ],
)
async def test_status_maps_transport_errors(code, state):
    _, hosts = make(FakeTmuxTransport(error=MuxroomError(code, "boom")))
    status = await hosts.status("local")
    assert status.state == state
    assert status.error is not None and status.error.code == code


async def test_status_tmux_missing():
    _, hosts = make(FakeTmuxTransport(tmux_installed=False))
    assert (await hosts.status("local")).state == "tmux_missing"


async def test_status_rejects_non_posix_shell():
    _, hosts = make(FakeTmuxTransport(shell="/usr/bin/fish"))
    assert (await hosts.status("local")).state == "error"


async def test_pane_cwd():
    fake = FakeTmuxTransport()
    session = fake.add_session("a", "/data/x")
    sessions, _ = make(fake)
    assert await sessions.pane_cwd("local", session.id) == "/data/x"


async def test_path_not_allowed_lists_allowed_folders():
    sessions, _ = make(FakeTmuxTransport())
    with pytest.raises(MuxroomError) as err:
        await sessions.create("local", CreateSession("x", "/home/u"))
    assert "Allowed: ~/proj (→ /home/u/proj), /data" in err.value.message
    assert "allowed_roots" in err.value.hint


async def test_rename_session():
    fake = FakeTmuxTransport()
    session = fake.add_session("0")
    fake.add_session("taken")
    sessions, _ = make(fake)
    renamed = await sessions.rename("local", session.id, "kim-bulk")
    assert renamed.id == session.id and renamed.name == "kim-bulk"
    assert ["tmux", "rename-session", "-t", session.id, "kim-bulk"] in fake.calls
    same = await sessions.rename("local", session.id, "kim-bulk")
    assert same.name == "kim-bulk"


@pytest.mark.parametrize(
    ("sid", "name", "code"),
    [
        ("$0", "taken", "session_exists"),
        ("$0", "bad name", "invalid_name"),
        ("$0", "a;rm -rf ~", "invalid_name"),
        ("$9", "x", "session_not_found"),
        ("0", "x", "session_not_found"),
    ],
)
async def test_rename_session_errors(sid, name, code):
    fake = FakeTmuxTransport()
    fake.add_session("0")
    fake.add_session("taken")
    sessions, _ = make(fake)
    with pytest.raises(MuxroomError) as err:
        await sessions.rename("local", sid, name)
    assert err.value.code == code
    assert fake.sessions["$0"].name == "0"


async def test_overview_reports_agent_states():
    fake = FakeTmuxTransport(agent_panes={"$0": ("node /x/claude", 1), "$1": ("/x/codex", 60)})
    fake.add_session("busy")
    fake.add_session("idle")
    fake.add_session("plain")
    sessions, _ = make(fake)
    rows = {r.session.name: r.agent for r in await sessions.overview("local")}
    assert (rows["busy"].agent_id, rows["busy"].state) == ("claude", "working")
    assert (rows["idle"].agent_id, rows["idle"].state) == ("codex", "waiting")
    assert rows["plain"] is None


async def test_overview_empty_and_missing_tmux():
    sessions, _ = make(FakeTmuxTransport())
    assert await sessions.overview("local") == []
    sessions, _ = make(FakeTmuxTransport(tmux_installed=False))
    with pytest.raises(MuxroomError) as err:
        await sessions.overview("local")
    assert err.value.code == "tmux_not_found"


def overview_calls(fake):
    return sum(1 for c in fake.calls if c[:2] == ["sh", "-c"] and c[2].startswith("t=$1; sf=$2"))


async def test_shared_overview_coalesces_and_expires(monkeypatch):
    import muxroom.services.sessions as mod

    fake = FakeTmuxTransport(delay=0.05)
    fake.add_session("a")
    sessions, _ = make(fake)
    results = await asyncio.gather(*(sessions.shared_overview("local") for _ in range(10)))
    assert overview_calls(fake) == 1 and all(len(r) == 1 for r in results)
    await sessions.shared_overview("local")  # within the sharing window
    assert overview_calls(fake) == 1
    monkeypatch.setattr(mod, "OVERVIEW_SHARE_SECONDS", 0.0)
    await sessions.shared_overview("local")
    assert overview_calls(fake) == 2


async def test_shared_overview_invalidated_by_create_and_never_caches_failures():
    fake = FakeTmuxTransport()
    sessions, _ = make(fake)
    assert await sessions.shared_overview("local") == []
    await sessions.create("local", CreateSession("new", "/data"))
    assert [r.session.name for r in await sessions.shared_overview("local")] == ["new"]

    fake.error = MuxroomError("host_unreachable", "down")
    sessions.invalidate("local")  # past the sharing window
    with pytest.raises(MuxroomError):
        await sessions.shared_overview("local")
    fake.error = None
    assert len(await sessions.shared_overview("local")) == 1


async def test_status_answered_from_recent_refresh():
    fake = FakeTmuxTransport()
    fake.add_session("a")
    sessions, hosts = make(fake)
    assert (await hosts.status("local")).state == "ok"  # first probe runs remotely

    def probes():
        return sum(
            1 for c in fake.calls if c[:2] == ["sh", "-c"] and c[2].startswith('printf "home=')
        )

    assert probes() == 1
    await sessions.shared_overview("local")
    status = await hosts.status("local")
    assert status.state == "ok" and status.tmux_version == "tmux 3.5a"
    assert probes() == 1  # no extra remote command

    fake.error = MuxroomError("host_unreachable", "down")
    sessions.invalidate("local")  # past the sharing window
    with pytest.raises(MuxroomError):
        await sessions.shared_overview("local")
    assert (await hosts.status("local")).state == "unreachable"  # failure forces a real probe
