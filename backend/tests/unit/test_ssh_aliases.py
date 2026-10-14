from muxroom.ssh_aliases import list_ssh_aliases


def test_aliases_with_includes_wildcards_and_cycles(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = tmp_path / ".ssh"
    (ssh / "conf.d").mkdir(parents=True)
    (ssh / "config").write_text(
        "Include conf.d/*.conf\n"
        "# Host commented\n"
        "Host *\n  ControlMaster auto\n"
        "Host gateway gateway.example.org\n  HostName gw\n"
        "Host=eq-alias\n"
        "Host !negated *.wild ?ingle box-1\n"
        "Match host foo\n  User x\n"
        "Host gateway\n"
        "Include config\n"  # cycle
    )
    (ssh / "conf.d" / "work.conf").write_text('Host "lab-mac" cluster\n')
    assert list_ssh_aliases(ssh / "config") == [
        "lab-mac",
        "cluster",
        "gateway",
        "gateway.example.org",
        "eq-alias",
        "box-1",
    ]


def test_missing_config_is_empty(tmp_path):
    assert list_ssh_aliases(tmp_path / "nope") == []
