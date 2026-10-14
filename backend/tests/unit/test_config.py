from pathlib import Path

import pytest

from muxroom.config import AppConfig, ConfigError, load_config, parse_config

EXAMPLE = """
[server]
port = 9000

[agents.claude]
label = "Claude Code"
command = ["claude"]

[[hosts]]
id = "local"

[[hosts]]
id = "work-mac"
transport = "openssh"
ssh_host = "work-mac"
tmux_path = "/opt/homebrew/bin/tmux"
allowed_roots = ["~/proj/", "/data"]
agents = ["claude"]
ssh_options = ["ServerAliveInterval=30"]

[hosts.files]
read_roots = ["~/proj", "/data"]
upload_roots = ["~/proj/incoming"]
"""


def test_parse_example():
    cfg = parse_config(EXAMPLE)
    assert cfg.server.port == 9000
    mac = cfg.host("work-mac")
    assert mac is not None
    assert mac.allowed_roots == ["~/proj", "/data"]
    assert mac.files.upload_roots == ["~/proj/incoming"]
    assert cfg.host_agents(mac) == ["claude"]
    assert cfg.host("missing") is None


def test_defaults_are_local_only():
    cfg = AppConfig()
    assert [h.id for h in cfg.hosts] == ["local"]
    assert set(cfg.agents) == {"claude", "codex", "opencode"}
    assert cfg.server.bind == "127.0.0.1"
    assert cfg.hosts[0].read_roots == ["~"]


@pytest.mark.parametrize(
    ("snippet", "fragment"),
    [
        ('[[hosts]]\nid = "Bad_Id"', "host id"),
        ('[[hosts]]\nid = "a"\n[[hosts]]\nid = "a"', "duplicate host ids"),
        ('[[hosts]]\nid = "r"\ntransport = "openssh"', "ssh_host is required"),
        ('[[hosts]]\nid = "r"\nssh_host = "x"', "need a remote transport"),
        ('[[hosts]]\nid = "r"\ntransport = "openssh"\nssh_host = "-oProxyCommand=x"', "ssh_host"),
        (
            '[[hosts]]\nid = "r"\ntransport = "asyncssh"\nssh_host = "x"\nssh_options = ["A=b"]',
            "only apply",
        ),
        (
            '[[hosts]]\nid = "r"\ntransport = "openssh"\nssh_host = "x"\nssh_options = ["bad"]',
            "Key=Value",
        ),
        ('[[hosts]]\nid = "a"\nagents = ["nope"]', "unknown agents"),
        ('[[hosts]]\nid = "a"\nallowed_roots = ["relative"]', "absolute"),
        ('[[hosts]]\nid = "a"\nallowed_roots = []', "must not be empty"),
        (
            '[[hosts]]\nid = "a"\n[hosts.files]\nread_roots = ["~/a"]\nupload_roots = ["~/b"]',
            "not inside a read root",
        ),
        ('[agents.shell]\nlabel = "x"\ncommand = ["sh"]', "invalid agent id"),
        ('[agents.x]\nlabel = "x"\ncommand = []', "command"),
        ("[server]\nunknown = 1", "server.unknown"),
        ("hosts = [", "invalid TOML"),
    ],
)
def test_invalid_configs(snippet, fragment):
    with pytest.raises(ConfigError) as err:
        parse_config(snippet)
    assert fragment in str(err.value)


def test_load_missing_default_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("MUXROOM_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg, path = load_config()
    assert path is None
    assert cfg.hosts[0].id == "local"


def test_load_explicit_missing_is_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_load_from_env(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text(EXAMPLE)
    monkeypatch.setenv("MUXROOM_CONFIG", str(p))
    cfg, path = load_config()
    assert path == p
    assert cfg.server.port == 9000


def test_shipped_example_config_is_valid():
    example = Path(__file__).resolve().parents[3] / "config.example.toml"
    cfg, _ = load_config(example)
    assert {h.transport for h in cfg.hosts} == {"local", "openssh"}
