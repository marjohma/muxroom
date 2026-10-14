import asyncio
from pathlib import Path

import pytest

from muxroom.errors import MuxroomError
from muxroom.services.sessions import CreateSession
from muxroom.tmux import commands as tc

pytestmark = pytest.mark.integration


async def wait_for(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


async def test_list_empty_without_server(services):
    sessions, _ = services
    assert await sessions.list("local") == []


async def test_status_ok(services):
    _, hosts = services
    status = await hosts.status("local")
    assert status.state == "ok", status.error
    assert status.tmux_version.startswith("tmux")


async def test_create_in_dir_with_spaces_and_quotes(services, workdir, tmux_server):
    sessions, _ = services
    odd = workdir / "a b'c\"d"
    odd.mkdir()
    session = await sessions.create("local", CreateSession("odd", str(odd)))
    path = tmux_server.cmd("display-message", "-p", "-t", session.id, "#{pane_current_path}")
    assert path.strip() == str(odd)


async def test_command_substitution_in_path_is_literal(services, workdir, tmux_server):
    sessions, _ = services
    tricky = workdir / "$(touch muxroom-pwned)`touch muxroom-pwned`"
    tricky.mkdir()
    session = await sessions.create("local", CreateSession("tricky", str(tricky)))
    await asyncio.sleep(0.3)
    for where in (workdir, tricky, Path.cwd(), Path.home()):
        assert not (where / "muxroom-pwned").exists()
    assert tmux_server.alive(session.id)


async def test_agent_launch_runs_inside_shell(services, workdir):
    sessions, _ = services
    session = await sessions.create("local", CreateSession("agent", str(workdir), agent="dummy"))
    assert session.name == "agent"
    assert await wait_for(lambda: (workdir / "agent-marker").exists())


async def test_duplicate_and_listing(services, workdir):
    sessions, _ = services
    await sessions.create("local", CreateSession("one", str(workdir)))
    await sessions.create("local", CreateSession("two", str(workdir)))
    with pytest.raises(MuxroomError) as err:
        await sessions.create("local", CreateSession("one", str(workdir)))
    assert err.value.code == "session_exists"
    assert sorted(s.name for s in await sessions.list("local")) == ["one", "two"]


async def test_path_outside_roots_rejected(services):
    sessions, _ = services
    with pytest.raises(MuxroomError) as err:
        await sessions.create("local", CreateSession("x", "/"))
    assert err.value.code == "path_not_allowed"


async def read_until(term, needle: bytes, timeout=5.0) -> bytes:
    buf = b""

    async def loop():
        nonlocal buf
        while needle not in buf:
            chunk = await term.read()
            if not chunk:
                break
            buf += chunk

    await asyncio.wait_for(loop(), timeout)
    return buf


async def test_terminal_io_resize_and_detach(services, workdir, tmux_server):
    sessions, hosts = services
    session = await sessions.create("local", CreateSession("term", str(workdir)))
    rt = hosts.get("local")
    term = await rt.transport.open_terminal(tc.attach(rt.tmux, session.id), cols=100, rows=30)
    try:
        assert await wait_for(lambda: len(tmux_server.clients(session.id)) == 1)
        await term.write(b"echo hello-$((40+2))\r")
        assert b"hello-42" in await read_until(term, b"hello-42")

        await term.resize(120, 40)

        def size():
            return tmux_server.cmd(
                "list-clients", "-t", session.id, "-F", "#{client_width}x#{client_height}"
            ).strip()

        assert await wait_for(lambda: size() == "120x40")
    finally:
        await term.close()
    assert await wait_for(lambda: tmux_server.clients(session.id) == [])
    assert tmux_server.alive(session.id)
    await term.close()  # idempotent


async def test_simultaneous_clients_and_no_implicit_detach(services, workdir, tmux_server):
    sessions, hosts = services
    session = await sessions.create("local", CreateSession("multi", str(workdir)))
    rt = hosts.get("local")
    argv = tc.attach(rt.tmux, session.id)
    first = await rt.transport.open_terminal(argv, cols=80, rows=24)
    second = await rt.transport.open_terminal(argv, cols=80, rows=24)
    try:
        assert await wait_for(lambda: len(tmux_server.clients(session.id)) == 2)
        await first.write(b"echo shared-output\r")
        await read_until(second, b"shared-output")
        await second.close()
        assert await wait_for(lambda: len(tmux_server.clients(session.id)) == 1)
    finally:
        await first.close()
        await second.close()
    assert tmux_server.alive(session.id)


async def test_rename_keeps_id_processes_and_attached_clients(services, workdir, tmux_server):
    sessions, hosts = services
    created = await sessions.create("local", CreateSession("0", str(workdir)))
    rt = hosts.get("local")
    term = await rt.transport.open_terminal(tc.attach(rt.tmux, created.id), cols=80, rows=24)
    try:
        assert await wait_for(lambda: len(tmux_server.clients(created.id)) == 1)
        renamed = await sessions.rename("local", created.id, "kim-bulk")
        assert renamed.id == created.id and renamed.created == created.created
        assert (
            tmux_server.cmd("display-message", "-p", "-t", created.id, "#{session_name}").strip()
            == "kim-bulk"
        )
        assert len(tmux_server.clients(created.id)) == 1
        await term.write(b"echo still-$((1+1))\r")
        await read_until(term, b"still-2")
    finally:
        await term.close()
    assert tmux_server.alive(created.id)


async def test_overview_detects_working_and_waiting_agents(tmux_server, workdir):
    from muxroom.config import AgentConfig, AppConfig, HostConfig
    from muxroom.services.hosts import HostService
    from muxroom.services.sessions import SessionService
    from muxroom.transport.local import LocalTransport

    busy = workdir / "busyagent"
    busy.write_text('#!/bin/sh\nwhile :; do printf "\\r%s" "$(date +%s)"; sleep 0.3; done\n')
    quiet = workdir / "quietagent"
    quiet.write_text('#!/bin/sh\nprintf "> "\nsleep 3600\n')
    for script in (busy, quiet):
        script.chmod(0o755)
    config = AppConfig(
        agents={
            "busy": AgentConfig(label="Busy", command=[str(busy)]),
            "quiet": AgentConfig(label="Quiet", command=[str(quiet)]),
        },
        hosts=[
            HostConfig(id="local", tmux_socket=tmux_server.socket, allowed_roots=[str(workdir)])
        ],
    )
    sessions = SessionService(HostService(config, lambda h: LocalTransport(h.id)))
    await sessions.create("local", CreateSession("busy", str(workdir), agent="busy"))
    await sessions.create("local", CreateSession("quiet", str(workdir), agent="quiet"))
    await sessions.create("local", CreateSession("plain", str(workdir)))
    await asyncio.sleep(6.5)  # quiet agent must be silent longer than the working window
    rows = {r.session.name: r.agent for r in await sessions.overview("local")}
    assert rows["busy"] is not None and rows["busy"].state == "working"
    assert rows["quiet"] is not None and rows["quiet"].state == "waiting"
    assert rows["plain"] is None


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="needs /proc (Linux)")
async def test_children_do_not_inherit_blocked_signals(services):
    from muxroom.transport.process import PtyTerminal, run_process

    script = "grep SigBlk /proc/$$/status"
    result = await run_process(["sh", "-c", script], timeout=5)
    assert result.stdout.split()[1] == "0000000000000000"
    term = await PtyTerminal.spawn(["sh", "-c", script + "; sleep 1"], cols=80, rows=24)
    try:
        output = await read_until(term, b"SigBlk")
        output += await term.read()
        assert b"0000000000000000" in output
    finally:
        await term.close()


async def test_tmux_formats_in_directory_names_stay_literal(services, workdir):
    sessions, _ = services
    tricky = workdir / "d#{pid}"
    tricky.mkdir()
    created = await sessions.create("local", CreateSession("fmt", str(tricky)))
    assert await sessions.pane_cwd("local", created.id) == str(tricky)
