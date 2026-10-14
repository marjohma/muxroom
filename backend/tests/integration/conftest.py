import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from muxroom.config import AgentConfig, AppConfig, HostConfig
from muxroom.services.hosts import HostService
from muxroom.services.sessions import SessionService
from muxroom.transport.local import LocalTransport

pytestmark = pytest.mark.integration

if shutil.which("tmux") is None:
    pytest.skip("tmux not installed", allow_module_level=True)


class TmuxServer:
    """A tmux server on a private socket, owned and torn down by the test."""

    def __init__(self, socket_name: str) -> None:
        self.socket = socket_name

    def cmd(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["tmux", "-L", self.socket, *args], capture_output=True, text=True, check=False
        )
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr)
        return result.stdout

    def clients(self, session_id: str) -> list[str]:
        out = self.cmd("list-clients", "-t", session_id, "-F", "#{client_tty}", check=False)
        return [line for line in out.splitlines() if line]

    def alive(self, session_id: str) -> bool:
        result = subprocess.run(
            ["tmux", "-L", self.socket, "has-session", "-t", session_id],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def teardown(self) -> None:
        # Test-owned private server only; never the user's default server.
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True, check=False)


@pytest.fixture
def tmux_server(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("ENV", "")
    monkeypatch.delenv("TMUX", raising=False)
    server = TmuxServer(f"muxroom-test-{uuid.uuid4().hex[:10]}")
    yield server
    server.teardown()


@pytest.fixture
def workdir(tmp_path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    return Path(os.path.realpath(root))


@pytest.fixture
def services(tmux_server, workdir):
    dummy_agent = workdir / "dummy agent.sh"
    dummy_agent.write_text('#!/bin/sh\necho started > "$(pwd)/agent-marker"\nexec sleep 3600\n')
    dummy_agent.chmod(0o755)
    config = AppConfig(
        agents={"dummy": AgentConfig(label="Dummy", command=[str(dummy_agent)])},
        hosts=[
            HostConfig(id="local", tmux_socket=tmux_server.socket, allowed_roots=[str(workdir)])
        ],
    )
    hosts = HostService(config, lambda h: LocalTransport(h.id))
    return SessionService(hosts), hosts
