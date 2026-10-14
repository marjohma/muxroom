import pytest

from muxroom.services.sessions import _split_sections
from muxroom.tmux.activity import match_agent, parse_activity

NAMES = {"claude": {"claude"}, "codex": {"codex"}, "opencode": {"opencode"}}


@pytest.mark.parametrize(
    ("args", "agent"),
    [
        ("node /Users/u/.local/bin/claude", "claude"),
        (
            "/Users/u/.nvm/versions/node/v24/bin/node /Users/u/.nvm/versions/node/v24/bin/codex",
            "codex",
        ),
        ("/home/u/.opencode/bin/opencode", "opencode"),
        ("claude --resume", "claude"),
        ("node --max-old-space-size=4096 /opt/claude", "claude"),
        ("vim claude.md", None),
        ("python3 train.py", None),
        ("-bash", None),
    ],
)
def test_match_agent(args, agent):
    assert match_agent(args, NAMES) == agent


def pane(sid, n, activity):
    return f"{sid}\t%{n}\t/dev/pts/{n}\t{activity}"


def test_parse_activity_states_and_aggregation():
    panes = "\n".join(
        [
            pane("$1", 1, 1000),  # claude, output 2 s ago -> working
            pane("$2", 2, 900),  # codex, silent 100 s -> waiting
            pane("$3", 3, 1000),  # plain shell
            pane("$4", 4, 500),  # two agents: one waiting ...
            pane("$4", 5, 999),  # ... one working -> session working
            "garbage line",
        ]
    )
    ps = "\n".join(
        [
            "pts/1 S+ node /x/claude",
            "pts/2 Ss+ /x/codex",
            "pts/3 Ss+ -bash",
            "pts/4 S+ claude",
            "pts/5 R+ opencode",
            "pts/6 S  claude",  # not foreground, no pane anyway
        ]
    )
    result = parse_activity(panes, "1002\n", ps, NAMES)
    assert {k: (v.agent_id, v.state) for k, v in result.items()} == {
        "$1": ("claude", "working"),
        "$2": ("codex", "waiting"),
        "$4": ("opencode", "working"),
    }


def test_background_agent_is_not_reported():
    result = parse_activity(
        pane("$1", 1, 1000), "1000", "pts/1 S node /x/claude\npts/1 S+ vim", NAMES
    )
    assert result == {}


def test_bad_clock_output_yields_nothing():
    assert parse_activity(pane("$1", 1, 1000), "", "pts/1 S+ claude", NAMES) == {}


def test_split_sections_handles_values_and_empty_sections():
    out = "@@muxroom@@rc=0\n$1\tx\n@@muxroom@@panes\n@@muxroom@@now\n42\n@@muxroom@@ps\n"
    sections = _split_sections(out)
    assert sections["rc"] == "0\n$1\tx"
    assert sections["panes"] == "" and sections["now"] == "42" and sections["ps"] == ""
