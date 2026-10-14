"""Opt-in checks against a real host: `make test-remote`.

Environment:
  MUXROOM_REMOTE_HOST       ssh alias (required)
  MUXROOM_REMOTE_TRANSPORT  openssh (default) | asyncssh
  MUXROOM_REMOTE_TMUX       tmux path on the host (default: tmux)

Creates one session named muxroom-test-<random> in the remote home and
removes exactly that session afterwards.
"""

import asyncio
import os
import shlex
import subprocess
import uuid

import pytest

from muxroom.config import AppConfig, HostConfig
from muxroom.services.hosts import HostService
from muxroom.services.sessions import CreateSession, SessionService
from muxroom.tmux import commands as tc
from muxroom.transport.factory import create_transport

pytestmark = pytest.mark.remote

ALIAS = os.environ.get("MUXROOM_REMOTE_HOST")
TMUX = os.environ.get("MUXROOM_REMOTE_TMUX", "tmux")
KIND = os.environ.get("MUXROOM_REMOTE_TRANSPORT", "openssh")

if not ALIAS:
    pytest.skip("MUXROOM_REMOTE_HOST not set", allow_module_level=True)


def remote_tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", ALIAS, shlex.join([TMUX, *args])],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
async def remote():
    host = HostConfig(
        id="remote", transport=KIND, ssh_host=ALIAS, tmux_path=TMUX, allowed_roots=["~"]
    )
    hosts = HostService(AppConfig(hosts=[host]), create_transport)
    name = f"muxroom-test-{uuid.uuid4().hex[:8]}"
    yield SessionService(hosts), hosts, name
    remote_tmux("kill-session", "-t", f"={name}")  # test-owned session only
    await hosts.aclose()


async def test_remote_roundtrip(remote):
    sessions, hosts, name = remote
    status = await hosts.status("remote")
    assert status.state == "ok", status.error
    created = await sessions.create("remote", CreateSession(name, "~"))
    assert created.name == name

    rt = hosts.get("remote")
    term = await rt.transport.open_terminal(tc.attach(rt.tmux, created.id), cols=100, rows=30)
    buf = b""
    try:
        await term.write(b"echo remote-$((40+2))\r")

        async def collect():
            nonlocal buf
            while b"remote-42" not in buf:
                chunk = await term.read()
                if not chunk:
                    break
                buf += chunk

        await asyncio.wait_for(collect(), 20)
        assert b"remote-42" in buf
    finally:
        await term.close()

    await asyncio.sleep(1.5)
    clients = remote_tmux("list-clients", "-t", created.id, "-F", "#{client_tty}").stdout.split()
    assert clients == []
    assert remote_tmux("has-session", "-t", created.id).returncode == 0
