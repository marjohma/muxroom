import asyncio
import json
import sys

import pytest

from muxroom.config import AppConfig, HostConfig
from muxroom.errors import MuxroomError
from muxroom.services.hosts import HostService
from muxroom.services.sessions import SessionService
from muxroom.services.terminals import TerminalService
from muxroom.transport.openssh import OpenSshTransport

pytestmark = pytest.mark.integration


class Socket:
    def __init__(self, inputs: list[dict]):
        self.inputs = asyncio.Queue()
        for item in inputs:
            self.inputs.put_nowait(item)
        self.text: list[dict] = []
        self.data = b""

    async def send_bytes(self, data):
        self.data += data

    async def send_text(self, data):
        self.text.append(json.loads(data))

    async def receive(self):
        return await self.inputs.get()


class FakeSsh(OpenSshTransport):
    """Replaces ssh with a script that asks for a 'password' and exits."""

    def __init__(self, host, master="auto", exit_code=0):
        super().__init__(host)
        self.master = master
        self.exit_code = exit_code

    def sign_in_argv(self):
        script = (
            "import sys; sys.stdout.write('Password: '); sys.stdout.flush(); "
            f"sys.stdin.readline(); sys.exit({self.exit_code})"
        )
        return [sys.executable, "-c", script]

    async def resolved_config(self):
        return {"controlmaster": self.master, "controlpath": "/tmp/cm-%C"}


def service(transport):
    host = HostConfig(id="r", transport="openssh", ssh_host="x")
    hosts = HostService(AppConfig(hosts=[host]), lambda _h: transport)
    return TerminalService(hosts, SessionService(hosts))


@pytest.mark.parametrize(("code", "reason"), [(0, "signed_in"), (1, "sign_in_failed")])
async def test_sign_in_flow(code, reason):
    host = HostConfig(id="r", transport="openssh", ssh_host="x")
    terminals = service(FakeSsh(host, exit_code=code))
    sock = Socket([{"type": "websocket.receive", "bytes": b"secret\r"}])
    await asyncio.wait_for(terminals.sign_in("r", sock, 80, 24), 10)
    assert b"Password:" in sock.data
    assert sock.text[-1] == {"type": "exit", "reason": reason}
    assert terminals.open_count == 0


async def test_sign_in_requires_control_master():
    host = HostConfig(id="r", transport="openssh", ssh_host="x")
    terminals = service(FakeSsh(host, master="no"))
    with pytest.raises(MuxroomError) as err:
        await terminals.sign_in("r", Socket([]), 80, 24)
    assert "ControlMaster" in err.value.hint
