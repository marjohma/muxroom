"""End-to-end invariant: muxroom restarts and crashes never end tmux sessions."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import websockets

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
DUMMY_WORKER = REPO / "scripts" / "dummy_worker.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, config: Path, port: int, state_dir: Path) -> None:
        self.config, self.port, self.state_dir = config, port, state_dir
        self.proc: subprocess.Popen | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "muxroom",
                "--config",
                str(self.config),
                "--log-level",
                "warning",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{self.base}/api/health", timeout=0.5).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def stop(self, sig: int) -> None:
        assert self.proc is not None
        self.proc.send_signal(sig)
        self.proc.wait(timeout=15)

    @property
    def token(self) -> str:
        return (self.state_dir / "token").read_text().strip()

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base,
            headers={"Origin": self.base, "Authorization": f"Bearer {self.token}"},
        )


@pytest.fixture
def server(tmp_path, tmux_server, workdir):
    port = free_port()
    state = tmp_path / "state"
    config = tmp_path / "config.toml"
    config.write_text(f"""
[server]
port = {port}
state_dir = "{state}"

[agents.worker]
label = "Dummy worker"
command = ["{sys.executable}", "{DUMMY_WORKER}", "{workdir}/counter"]

[[hosts]]
id = "local"
tmux_socket = "{tmux_server.socket}"
allowed_roots = ["{workdir}"]
""")
    srv = Server(config, port, state)
    srv.start()
    yield srv
    if srv.proc and srv.proc.poll() is None:
        srv.stop(signal.SIGTERM)


def counter(workdir: Path) -> int:
    try:
        return int((workdir / "counter").read_text().split()[0])
    except (FileNotFoundError, ValueError, IndexError):
        return 0


async def wait_until(predicate, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return predicate()


async def attach(srv: Server, session_id: str):
    url = (
        f"ws://127.0.0.1:{srv.port}/api/hosts/local/sessions/"
        f"{session_id.replace('$', '%24')}/terminal?cols=80&rows=24"
    )
    ws = await websockets.connect(
        url, origin=srv.base, subprotocols=["muxroom", f"muxroom.token.{srv.token}"]
    )
    assert json.loads(await ws.recv())["type"] == "attached"
    return ws


async def assert_session_survives(tmux_server, workdir, session_id):
    assert await wait_until(lambda: tmux_server.clients(session_id) == [])
    assert tmux_server.alive(session_id)
    before = counter(workdir)
    assert await wait_until(lambda: counter(workdir) >= before + 2, timeout=5)


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGKILL, signal.SIGINT])
async def test_backend_stop_keeps_session_and_reattach_works(server, tmux_server, workdir, sig):
    with server.client() as c:
        r = c.post(
            "/api/hosts/local/sessions",
            json={"name": "persist", "cwd": str(workdir), "agent": "worker"},
        )
        assert r.status_code == 201, r.text
        session_id = r.json()["id"]
    assert await wait_until(lambda: counter(workdir) >= 1)

    ws = await attach(server, session_id)
    assert await wait_until(lambda: len(tmux_server.clients(session_id)) == 1)
    if sig == signal.SIGINT:
        # Ctrl+C reaches the whole process group of the backend
        os.killpg(server.proc.pid, signal.SIGINT)
        server.proc.wait(timeout=15)
    else:
        server.stop(sig)
    await ws.close()
    await assert_session_survives(tmux_server, workdir, session_id)

    server.start()
    ws = await attach(server, session_id)
    await ws.send(b"\x02")  # harmless input (tmux prefix) proves the pipe is live
    assert await wait_until(lambda: len(tmux_server.clients(session_id)) == 1)
    await ws.close()
    await assert_session_survives(tmux_server, workdir, session_id)


async def test_browser_disconnect_detaches_only_its_client(server, tmux_server, workdir):
    with server.client() as c:
        session_id = c.post(
            "/api/hosts/local/sessions",
            json={"name": "drop", "cwd": str(workdir), "agent": "worker"},
        ).json()["id"]
    first = await attach(server, session_id)
    second = await attach(server, session_id)
    assert await wait_until(lambda: len(tmux_server.clients(session_id)) == 2)
    first.transport.abort() if hasattr(first, "transport") else await first.close()
    assert await wait_until(lambda: len(tmux_server.clients(session_id)) == 1)
    await second.close()
    await assert_session_survives(tmux_server, workdir, session_id)


def rss_kib(pid: int) -> int:
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout
    return int(out.strip() or 0)


async def test_large_download_streams_with_bounded_memory(server, workdir):
    big = workdir / "big.bin"
    with big.open("wb") as handle:
        handle.truncate(1024**3)  # sparse 1 GiB
    baseline = rss_kib(server.proc.pid)
    peak = baseline
    received = 0
    with (
        server.client() as c,
        c.stream("GET", "/api/hosts/local/files/content", params={"path": str(big)}) as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-length"] == str(1024**3)
        for i, chunk in enumerate(response.iter_bytes(1024 * 1024)):
            received += len(chunk)
            if i % 64 == 0:
                peak = max(peak, rss_kib(server.proc.pid))
    assert received == 1024**3
    assert (peak - baseline) < 64 * 1024, f"RSS grew by {(peak - baseline) // 1024} MiB"
