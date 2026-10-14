import os
import stat

import pytest

from muxroom.config import parse_config
from muxroom.errors import MuxroomError
from muxroom.services.config_manager import ConfigManager, HostDraft
from muxroom.services.files import FileService
from muxroom.services.hosts import HostService
from muxroom.services.sessions import SessionService
from tests.fakes import FakeTmuxTransport

BASE = """# my hand-written config
[server]
port = 8765  # keep this comment

[[hosts]]
id = "local"
label = "Laptop"
"""


def fake_factory(created):
    def factory(host):
        kwargs = {}
        if host.ssh_host == "down":
            kwargs["error"] = MuxroomError("host_unreachable", "no route", hint="check VPN")
        if host.ssh_host == "nokey":
            kwargs["error"] = MuxroomError("ssh_auth_failed", "Permission denied")
        if host.ssh_host == "notmux":
            kwargs["tmux_installed"] = False
        transport = FakeTmuxTransport(host.id, **kwargs)
        created.append(transport)
        return transport

    return factory


@pytest.fixture
def env(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(BASE)
    os.chmod(cfg, 0o640)
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host lab down nokey notmux\n")
    created = []
    factory = fake_factory(created)
    config = parse_config(BASE)
    hosts = HostService(config, factory)
    files = FileService(hosts, SessionService(hosts), config.files)
    seen = []
    manager = ConfigManager(cfg, hosts, files, factory, seen.append, ssh_config_path=ssh_config)
    return manager, cfg, created, seen


def draft(**kw):
    return HostDraft(**{"id": "lab", "ssh_host": "lab", "label": "Lab Mac", **kw})


async def test_add_host_appends_and_preserves_comments(env):
    manager, cfg, _, seen = env
    _host, result = await manager.add_host(
        draft(tmux_path="/opt/homebrew/bin/tmux", agents=["claude"], upload_roots=["~/in"])
    )
    text = cfg.read_text()
    assert "# my hand-written config" in text and "# keep this comment" in text
    assert 'ssh_host = "lab"' in text and "[hosts.files]" in text
    assert result.added == ["lab"]
    assert [h.id for h in manager.config.hosts] == ["local", "lab"]
    assert seen[-1].host("lab").tmux_path == "/opt/homebrew/bin/tmux"
    assert (cfg.parent / "config.toml.bak").read_text() == BASE
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o640
    assert parse_config(text).host("lab").files.upload_roots == ["~/in"]


async def test_add_host_needing_sign_in_is_allowed(env):
    manager, _, _, _ = env
    host, _ = await manager.add_host(draft(id="needs-pw", ssh_host="nokey"))
    assert host.id == "needs-pw"


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"ssh_host": "not-in-ssh-config"}, "unknown_ssh_alias"),
        ({"id": "local"}, "host_exists"),
        ({"id": "Bad Id"}, "invalid_request"),
        ({"tmux_path": "tmux; rm -rf ~"}, "invalid_request"),
        ({"tmux_path": "relative/tmux"}, "invalid_request"),
        ({"transport": "local"}, "invalid_request"),
        ({"agents": ["nope"]}, "invalid_request"),
        ({"allowed_roots": ["relative"]}, "invalid_request"),
        ({"ssh_host": "down"}, "connection_test_failed"),
        ({"ssh_host": "notmux"}, "connection_test_failed"),
    ],
)
async def test_add_host_rejections_leave_file_untouched(env, kw, code):
    manager, cfg, _, _ = env
    with pytest.raises(MuxroomError) as err:
        await manager.add_host(draft(**kw))
    assert err.value.code == code
    assert cfg.read_text() == BASE
    assert [h.id for h in manager.config.hosts] == ["local"]


async def test_connection_test_suggests_tmux_path(env):
    manager, _, _, _ = env
    result = await manager.test_host(draft(ssh_host="notmux"))
    assert result.status.state == "tmux_missing"
    assert result.tmux_suggestion == "/opt/homebrew/bin/tmux"
    ok = await manager.test_host(draft())
    assert ok.status.state == "ok" and ok.tmux_suggestion is None


async def test_editing_can_be_disabled(env):
    manager, cfg, _, _ = env
    cfg.write_text(BASE.replace("port = 8765", "port = 8765\nallow_host_editing = false"))
    await manager.reload()
    with pytest.raises(MuxroomError) as err:
        await manager.add_host(draft())
    assert err.value.code == "host_editing_disabled"


async def test_add_host_without_hosts_section_keeps_local(env):
    manager, cfg, _, _ = env
    cfg.write_text("[server]\nport = 8765\n")
    await manager.reload()
    await manager.add_host(draft())
    assert [h.id for h in parse_config(cfg.read_text()).hosts] == ["local", "lab"]


async def test_add_host_creates_missing_file(env):
    manager, cfg, _, _ = env
    cfg.unlink()
    await manager.reload()
    await manager.add_host(draft())
    assert [h.id for h in parse_config(cfg.read_text()).hosts] == ["local", "lab"]
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600


async def test_reload_keeps_unchanged_transports(env):
    manager, cfg, created, _ = env
    local_before = manager.hosts.get("local").transport
    cfg.write_text(BASE + '\n[[hosts]]\nid = "lab"\ntransport = "openssh"\nssh_host = "lab"\n')
    result = await manager.reload()
    assert result.added == ["lab"] and result.removed == [] and result.changed == []
    assert manager.hosts.get("local").transport is local_before

    cfg.write_text(BASE.replace('label = "Laptop"', 'label = "Renamed"'))
    result = await manager.reload()
    assert result.removed == ["lab"] and result.changed == ["local"]
    assert local_before.closed
    assert any(t.host_id == "lab" and t.closed for t in created)


async def test_invalid_reload_keeps_previous_config(env):
    manager, cfg, _, _ = env
    cfg.write_text("[[hosts]]\nid = 'BAD'\n")
    with pytest.raises(MuxroomError) as err:
        await manager.reload()
    assert err.value.code == "invalid_config"
    assert "host id" in err.value.message
    assert [h.id for h in manager.config.hosts] == ["local"]


async def test_reload_reports_server_changes_needing_restart(env):
    manager, cfg, _, _ = env
    cfg.write_text(BASE.replace("port = 8765", "port = 9999"))
    result = await manager.reload()
    assert result.restart_required == ["server.port"]


async def test_symlinked_config_stays_a_symlink(env, tmp_path):
    manager, cfg, _, _ = env
    real = tmp_path / "dotfiles" / "muxroom.toml"
    real.parent.mkdir()
    real.write_text(cfg.read_text())
    cfg.unlink()
    cfg.symlink_to(real)
    await manager.add_host(draft())
    assert cfg.is_symlink()
    assert 'ssh_host = "lab"' in real.read_text()
