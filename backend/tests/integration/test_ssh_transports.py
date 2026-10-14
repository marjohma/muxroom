"""OpenSSH and AsyncSSH transports against an in-process SSH server + real tmux."""

import asyncio
import shutil

import pytest

from muxroom.config import AppConfig, HostConfig
from muxroom.services.hosts import HostService
from muxroom.services.sessions import CreateSession, SessionService
from muxroom.tmux import commands as tc
from muxroom.transport.factory import create_transport
from muxroom.transport.openssh import OpenSshTransport
from muxroom.transport.sftp_pipe import start_pipe_sftp_client
from tests.integration.test_local_tmux import read_until, wait_for
from tests.sshserver import ALIAS, start_server

pytestmark = pytest.mark.integration

KINDS = ["openssh", "asyncssh"]


def needs_ssh(kind):
    if kind == "openssh" and shutil.which("ssh") is None:
        pytest.skip("OpenSSH client not installed")


@pytest.fixture
async def ssh_server(tmp_path):
    server = await start_server(tmp_path / "ssh")
    yield server
    server.close()


def build(kind, ssh_config, tmux_server, workdir, **extra):
    host = HostConfig(
        id="remote",
        transport=kind,
        ssh_host=ALIAS,
        ssh_config=str(ssh_config),
        tmux_socket=tmux_server.socket,
        allowed_roots=[str(workdir)],
        connect_timeout=5,
        **extra,
    )
    hosts = HostService(AppConfig(hosts=[host]), create_transport)
    return SessionService(hosts), hosts


@pytest.fixture(params=KINDS)
async def remote(request, ssh_server, tmux_server, workdir):
    needs_ssh(request.param)
    sessions, hosts = build(request.param, ssh_server.config_file, tmux_server, workdir)
    yield sessions, hosts
    await hosts.aclose()


async def test_status_and_session_roundtrip(remote, tmux_server, workdir):
    sessions, hosts = remote
    status = await hosts.status("remote")
    assert status.state == "ok", status.error
    assert await sessions.list("remote") == []
    odd = workdir / "dir with 'quotes' and $HOME"
    odd.mkdir()
    created = await sessions.create("remote", CreateSession("viassh", str(odd)))
    assert await sessions.pane_cwd("remote", created.id) == str(odd)
    assert [s.name for s in await sessions.list("remote")] == ["viassh"]


async def test_terminal_over_ssh(remote, tmux_server, workdir):
    sessions, hosts = remote
    created = await sessions.create("remote", CreateSession("t", str(workdir)))
    rt = hosts.get("remote")
    argv = tc.attach(rt.tmux, created.id)
    term = await rt.transport.open_terminal(argv, cols=90, rows=30)
    other = await rt.transport.open_terminal(argv, cols=90, rows=30)
    try:
        assert await wait_for(lambda: len(tmux_server.clients(created.id)) == 2, timeout=10)
        await term.write(b"echo over-$((6*7))\r")
        await read_until(other, b"over-42", timeout=10)
        await term.resize(110, 35)

        def size():
            out = tmux_server.cmd(
                "list-clients", "-t", created.id, "-F", "#{client_width}x#{client_height}"
            )
            return out.split()

        assert await wait_for(lambda: "110x35" in size(), timeout=10)
    finally:
        await term.close()
    assert await wait_for(lambda: len(tmux_server.clients(created.id)) == 1, timeout=10)
    await other.close()
    assert await wait_for(lambda: tmux_server.clients(created.id) == [], timeout=10)
    assert tmux_server.alive(created.id)


@pytest.mark.parametrize("kind", KINDS)
async def test_auth_failure_is_reported(kind, tmp_path, tmux_server, workdir):
    needs_ssh(kind)
    server = await start_server(tmp_path / "noauth", authorize=False)
    try:
        _, hosts = build(kind, server.config_file, tmux_server, workdir)
        status = await hosts.status("remote")
        assert status.state == "auth_failed", status.error
        assert status.error.hint
    finally:
        server.close()


@pytest.mark.parametrize("kind", KINDS)
async def test_unknown_host_key_is_reported(kind, tmp_path, ssh_server, tmux_server, workdir):
    needs_ssh(kind)
    (tmp_path / "ssh" / "known_hosts").write_text("")
    _, hosts = build(kind, ssh_server.config_file, tmux_server, workdir)
    status = await hosts.status("remote")
    assert status.state == "host_key", status.error


@pytest.mark.parametrize("kind", KINDS)
async def test_unreachable_host(kind, tmp_path, tmux_server, workdir):
    needs_ssh(kind)
    server = await start_server(tmp_path / "gone")
    server.close()
    await asyncio.sleep(0.1)
    _, hosts = build(kind, server.config_file, tmux_server, workdir)
    status = await hosts.status("remote")
    assert status.state == "unreachable", status.error


async def test_sftp_over_openssh_pipe(ssh_server, tmux_server, workdir):
    needs_ssh("openssh")
    (workdir / "plot.png").write_bytes(b"\x89PNG" + b"x" * 100_000)
    host = HostConfig(
        id="remote", transport="openssh", ssh_host=ALIAS, ssh_config=str(ssh_server.config_file)
    )
    transport = OpenSshTransport(host)
    proc = await transport.open_sftp_process()
    client, receiver = await start_pipe_sftp_client(proc)
    try:
        assert await client.realpath(str(workdir)) == str(workdir)
        assert "plot.png" in await client.listdir(str(workdir))
        async with client.open(str(workdir / "plot.png"), "rb") as f:
            data = await f.read()
        assert len(data) == 100_004
    finally:
        client.exit()
        await asyncio.wait_for(proc.wait(), 5)
        receiver.cancel()
