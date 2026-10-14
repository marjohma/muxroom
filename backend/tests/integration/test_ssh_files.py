"""SFTP file access through both remote transports (in-process SFTP server)."""

import os
from pathlib import Path

import pytest

from muxroom.config import AppConfig, FileLimits, HostConfig, HostFiles
from muxroom.services.files import FileService
from muxroom.services.hosts import HostService
from muxroom.services.sessions import SessionService
from muxroom.transport.factory import create_transport
from tests.integration.test_ssh_transports import KINDS, needs_ssh
from tests.sshserver import ALIAS, start_server
from tests.unit.test_files import PNG

pytestmark = pytest.mark.integration


@pytest.fixture(params=KINDS)
async def files_env(request, tmp_path):
    needs_ssh(request.param)
    server = await start_server(tmp_path / "ssh")
    root = Path(os.path.realpath(tmp_path)) / "data"
    (root / "plots").mkdir(parents=True)
    (root / "incoming").mkdir()
    (root / "plots" / "fig.png").write_bytes(PNG)
    (root / "plots" / "escape").symlink_to("/etc")
    host = HostConfig(
        id="remote",
        transport=request.param,
        ssh_host=ALIAS,
        ssh_config=str(server.config_file),
        connect_timeout=5,
        tmux_path="/nonexistent/tmux",  # files must work without tmux
        allowed_roots=[str(root)],
        files=HostFiles(upload_roots=[str(root / "incoming")]),
    )
    hosts = HostService(AppConfig(hosts=[host], files=FileLimits()), create_transport)
    service = FileService(hosts, SessionService(hosts), FileLimits())
    yield service, root
    await hosts.aclose()
    server.close()


async def collect(chunks):
    return b"".join([c async for c in chunks])


async def test_browse_preview_upload(files_env):
    service, root = files_env
    roots = await service.roots("remote")
    assert [r.path for r in roots] == [str(root)]
    listing = await service.list("remote", str(root / "plots"))
    by_name = {e.name: e for e in listing.entries}
    assert by_name["fig.png"].preview == "png"
    assert by_name["escape"].outside_roots

    opened = await service.open_read("remote", str(root / "plots" / "fig.png"), inline=True)
    assert await collect(opened.chunks) == PNG

    async def body():
        yield b"hello "
        yield b"world"

    info = await service.upload(
        "remote", str(root / "incoming"), "note.txt", body(), overwrite=False
    )
    assert info.size == 11
    assert (root / "incoming" / "note.txt").read_bytes() == b"hello world"
    assert [p.name for p in (root / "incoming").iterdir()] == ["note.txt"]


async def test_reconnect_after_file_connection_drop(files_env):
    service, root = files_env
    await service.stat("remote", str(root / "plots" / "fig.png"))
    files = service.hosts.get("remote").transport.files()
    await files.close()  # simulate a dropped file connection
    info = await service.stat("remote", str(root / "plots" / "fig.png"))
    assert info.size == len(PNG)
