import pytest

from muxroom.errors import MuxroomError
from muxroom.tmux import commands as tc
from muxroom.tmux.parser import (
    is_no_server,
    parse_sessions,
    parse_version,
    raise_for_tmux,
)
from muxroom.transport.base import CommandResult

T = tc.TmuxTarget("/opt/tmux", "sock")


def line(
    sid="$1",
    created="1700000000",
    attached="0",
    windows="2",
    activity="1700000100",
    path="/home/u",
    name="demo",
):
    return "\t".join([sid, created, attached, windows, activity, path, name])


def test_parse_sessions_basic():
    out = line() + "\n" + line(sid="$7", attached="2", name="other") + "\n"
    sessions = parse_sessions(out)
    assert [s.id for s in sessions] == ["$1", "$7"]
    assert sessions[1].attached_clients == 2
    assert sessions[0].windows == 2
    assert sessions[0].created.timestamp() == 1700000000
    assert sessions[0].path == "/home/u"


def test_parse_sessions_empty():
    assert parse_sessions("") == []


def test_parse_sessions_odd_names_keep_tabs_and_unicode():
    sessions = parse_sessions(line(name="a b\tc ü 🚀"))
    assert sessions[0].name == "a b\tc ü 🚀"


def test_parse_sessions_skips_malformed():
    out = "garbage\n" + line(windows="x") + "\n" + line(name="ok") + "\n"
    assert [s.name for s in parse_sessions(out)] == ["ok"]


def test_parse_sessions_empty_path_and_activity():
    s = parse_sessions(line(path="", activity=""))[0]
    assert s.path == ""
    assert s.last_activity == s.created


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tmux 3.5a", (3, 5)),
        ("tmux 3.7c\n", (3, 7)),
        ("tmux next-3.6", (3, 6)),
        ("tmux master", None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_no_server_detection():
    assert is_no_server(CommandResult(1, "", "no server running on /tmp/tmux-1/default"))
    assert is_no_server(
        CommandResult(1, "", "error connecting to /tmp/x (No such file or directory)")
    )
    assert not is_no_server(CommandResult(0, "", ""))


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (CommandResult(127, "", "sh: tmux: command not found"), "tmux_not_found"),
        (CommandResult(1, "", "bash: /opt/tmux: No such file or directory"), "tmux_not_found"),
        (CommandResult(1, "", "duplicate session: demo"), "session_exists"),
        (CommandResult(1, "", "can't find session: $9"), "session_not_found"),
        (CommandResult(1, "", "something odd"), "tmux_error"),
    ],
)
def test_raise_for_tmux(result, code):
    with pytest.raises(MuxroomError) as err:
        raise_for_tmux(result, "/opt/tmux", "h")
    assert err.value.code == code


def test_raise_for_tmux_success_is_noop():
    raise_for_tmux(CommandResult(0, "", ""), "tmux", "h")


def test_builders_produce_argv_with_socket():
    assert tc.list_sessions(T)[:4] == ["/opt/tmux", "-L", "sock", "list-sessions"]
    assert tc.has_session(T, "x") == ["/opt/tmux", "-L", "sock", "has-session", "-t", "=x"]
    assert tc.new_session(T, "n", "/a b")[3:9] == ["new-session", "-d", "-s", "n", "-c", "/a b"]
    assert tc.send_literal(T, "$3", "claude --x")[-4:] == ["-t", "$3", "-l", "claude --x"]


def test_attach_never_detaches_others():
    argv = tc.attach(T, "$3")
    assert argv == ["/opt/tmux", "-u", "-L", "sock", "attach-session", "-t", "$3"]
    assert "-d" not in argv


def test_builders_without_socket():
    assert tc.list_sessions(tc.TmuxTarget())[:2] == ["tmux", "list-sessions"]
