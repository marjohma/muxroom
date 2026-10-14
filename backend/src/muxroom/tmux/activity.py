"""Agent activity detection from tmux and ps output (pure parsing).

A pane runs an agent when its terminal's foreground process matches a
configured agent. It is *working* while the pane keeps producing output
(agents animate a spinner while busy) and *waiting* once it has been silent
for a few seconds, which is when it expects user input.
"""

import posixpath
from dataclasses import dataclass
from typing import Literal

AgentState = Literal["working", "waiting"]
WORKING_WINDOW_SECONDS = 4
INTERPRETERS = {"node", "nodejs", "bun", "deno", "python", "python3", "sh", "bash", "zsh", "env"}
PANE_SEP = "\t"
PANE_FORMAT = PANE_SEP.join(["#{session_id}", "#{pane_id}", "#{pane_tty}", "#{window_activity}"])


@dataclass(frozen=True, slots=True)
class AgentActivity:
    agent_id: str
    state: AgentState


def _program_names(args: str) -> list[str]:
    """Candidate program names of a command line (looks through interpreters)."""
    tokens = args.split()
    names: list[str] = []
    for token in tokens[:3]:
        if token.startswith("-"):
            continue
        name = posixpath.basename(token)
        names.append(name)
        if name not in INTERPRETERS:
            break
    return names


def match_agent(args: str, process_names: dict[str, set[str]]) -> str | None:
    for name in _program_names(args):
        for agent_id, names in process_names.items():
            if name in names:
                return agent_id
    return None


def parse_activity(
    panes_out: str,
    now_out: str,
    ps_out: str,
    process_names: dict[str, set[str]],
) -> dict[str, AgentActivity]:
    """Per-session agent activity; sessions without a detected agent are omitted."""
    try:
        now = int(now_out.strip())
    except ValueError:
        return {}
    foreground: dict[str, list[str]] = {}
    for line in ps_out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and "+" in parts[1]:
            foreground.setdefault(parts[0], []).append(parts[2])

    result: dict[str, AgentActivity] = {}
    for line in panes_out.splitlines():
        parts = line.split(PANE_SEP)
        if len(parts) != 4:
            continue
        session_id, _pane, tty, activity = parts
        tty = tty.removeprefix("/dev/")
        agent = next(
            (a for args in foreground.get(tty, []) if (a := match_agent(args, process_names))),
            None,
        )
        if agent is None:
            continue
        try:
            silent_for = now - int(activity)
        except ValueError:
            continue
        state: AgentState = "working" if silent_for <= WORKING_WINDOW_SECONDS else "waiting"
        previous = result.get(session_id)
        if previous is None or (state == "working" and previous.state != "working"):
            result[session_id] = AgentActivity(agent, state)
    return result
