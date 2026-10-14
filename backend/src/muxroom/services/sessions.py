"""tmux session discovery and creation."""

import asyncio
import shlex
import time
from dataclasses import dataclass

from muxroom.errors import MuxroomError
from muxroom.files.policy import describe_roots
from muxroom.services.hosts import HostService
from muxroom.tmux import commands as tc
from muxroom.tmux.activity import PANE_FORMAT, AgentActivity, parse_activity
from muxroom.tmux.parser import TmuxSession, is_no_server, parse_sessions, raise_for_tmux
from muxroom.transport.base import CommandResult
from muxroom.validation import (
    is_within,
    validate_session_id,
    validate_session_name,
    validate_user_path,
)

SHELL_AGENT = "shell"
_MARK = "@@muxroom@@"
# One round trip: sessions (with exit status), panes, host clock, and the
# foreground processes of the panes' terminals. $1 = tmux path,
# $2/$3 = formats, remaining args = tmux global options (e.g. -L socket).
_OVERVIEW_SCRIPT = (
    "t=$1; sf=$2; pf=$3; shift 3; "
    'out=$("$t" "$@" list-sessions -F "$sf" 2>&1); rc=$?; '
    f'echo "{_MARK}rc=$rc"; printf "%s\n" "$out"; echo "{_MARK}panes"; '
    'panes=$("$t" "$@" list-panes -a -F "$pf" 2>/dev/null); printf "%s\n" "$panes"; '
    f'echo "{_MARK}now"; date +%s; echo "{_MARK}ps"; '
    # Only the panes' terminals: far cheaper than scanning every process.
    'for tty in $(printf "%s\n" "$panes" | cut -f3 | sed "s#^/dev/##" | sort -u); do '
    "ps -o tty= -o stat= -o args= -t \"$tty\" 2>/dev/null; done | awk '$2 ~ /[+]/'"
)


@dataclass(frozen=True, slots=True)
class SessionOverview:
    session: TmuxSession
    agent: AgentActivity | None


@dataclass(frozen=True, slots=True)
class CreateSession:
    name: str
    cwd: str
    agent: str = SHELL_AGENT


OVERVIEW_SHARE_SECONDS = 1.5


class SessionService:
    def __init__(self, hosts: HostService) -> None:
        self.hosts = hosts
        self._overviews: dict[str, tuple[float, asyncio.Future[list[SessionOverview]]]] = {}

    async def shared_overview(self, host_id: str) -> "list[SessionOverview]":
        """Overview shared by concurrent and closely spaced callers (windows, tabs)."""
        self.hosts.get(host_id)
        now = time.monotonic()
        entry = self._overviews.get(host_id)
        if entry is not None:
            started, future = entry
            if not future.done() or now - started < OVERVIEW_SHARE_SECONDS:
                return await asyncio.shield(future)
        future = asyncio.ensure_future(self._timed_overview(host_id))
        self._overviews[host_id] = (now, future)
        try:
            return await asyncio.shield(future)
        except Exception:
            self._overviews.pop(host_id, None)  # never cache failures
            raise

    async def _timed_overview(self, host_id: str) -> "list[SessionOverview]":
        started = time.monotonic()
        try:
            rows = await self.overview(host_id)
        except MuxroomError:
            self.hosts.note_failure(host_id)
            raise
        self.hosts.note_success(host_id, int((time.monotonic() - started) * 1000))
        return rows

    def invalidate(self, host_id: str) -> None:
        self._overviews.pop(host_id, None)

    async def list(self, host_id: str) -> list[TmuxSession]:
        rt = self.hosts.get(host_id)
        result = await rt.transport.run(
            tc.list_sessions(rt.tmux), timeout=rt.config.command_timeout
        )
        if is_no_server(result):
            return []
        raise_for_tmux(result, rt.config.tmux_path, host_id)
        return parse_sessions(result.stdout)

    async def overview(self, host_id: str) -> "list[SessionOverview]":
        """Sessions plus agent activity, in a single command on the host."""
        rt = self.hosts.get(host_id)
        result = await self.hosts.run_sh(
            host_id,
            _OVERVIEW_SCRIPT,
            rt.config.tmux_path,
            tc.SESSION_FORMAT,
            PANE_FORMAT,
            *rt.tmux.base()[1:],
        )
        sections = _split_sections(result.stdout)
        if "rc" not in sections:
            raise MuxroomError(
                "ssh_error", result.stderr.strip() or "session overview failed", host_id=host_id
            )
        rc_line, _, listing = sections["rc"].partition("\n")
        listed = CommandResult(int(rc_line or 1), listing, listing)
        if is_no_server(listed):
            return []
        raise_for_tmux(listed, rt.config.tmux_path, host_id)
        names = {k: a.detection_names for k, a in self.hosts.config.agents.items()}
        activity = parse_activity(
            sections.get("panes", ""), sections.get("now", ""), sections.get("ps", ""), names
        )
        return [SessionOverview(s, activity.get(s.id)) for s in parse_sessions(listing)]

    async def get(self, host_id: str, session_id: str) -> TmuxSession:
        validate_session_id(session_id)
        for session in await self.list(host_id):
            if session.id == session_id:
                return session
        raise MuxroomError(
            "session_not_found", f"No session {session_id} on {host_id}", host_id=host_id
        )

    async def resolve_cwd(self, host_id: str, cwd: str) -> str:
        """Validate a start directory and return its canonical path on the host."""
        rt = self.hosts.get(host_id)
        validate_user_path(cwd)
        expanded = await self.hosts.expand(host_id, cwd)
        roots = await self.hosts.canonical_roots(host_id, list(rt.config.allowed_roots))
        (canonical,) = await self.hosts.realpath_dirs(host_id, [expanded])
        if canonical is None:
            raise MuxroomError(
                "path_not_found", f"Directory not found: {expanded}", host_id=host_id
            )
        if not any(is_within(canonical, root) for root in roots):
            pairs = await self.hosts.root_pairs(host_id, list(rt.config.allowed_roots))
            raise MuxroomError(
                "path_not_allowed",
                f"{canonical} is outside the folders where sessions may start on this host. "
                f"Allowed: {describe_roots(pairs)}",
                host_id=host_id,
                hint=f"Edit allowed_roots for host '{host_id}' in config.toml, "
                "then click Reload config.",
            )
        return canonical

    async def create(self, host_id: str, request: CreateSession) -> TmuxSession:
        rt = self.hosts.get(host_id)
        validate_session_name(request.name)
        agent_command: list[str] | None = None
        if request.agent != SHELL_AGENT:
            if request.agent not in self.hosts.config.host_agents(rt.config):
                raise MuxroomError(
                    "agent_not_allowed",
                    f"Agent {request.agent!r} is not enabled for {host_id}",
                    host_id=host_id,
                )
            agent_command = list(self.hosts.config.agents[request.agent].command)
        cwd = await self.resolve_cwd(host_id, request.cwd)

        timeout = rt.config.command_timeout
        exists = await rt.transport.run(tc.has_session(rt.tmux, request.name), timeout=timeout)
        if exists.exit_status == 0:
            raise MuxroomError(
                "session_exists", f"Session {request.name!r} already exists", host_id=host_id
            )
        created = await rt.transport.run(
            tc.new_session(rt.tmux, request.name, cwd), timeout=timeout
        )
        raise_for_tmux(created, rt.config.tmux_path, host_id)
        session_id = created.stdout.strip()
        validate_session_id(session_id)

        if agent_command:
            for argv in (
                tc.send_literal(rt.tmux, session_id, shlex.join(agent_command)),
                tc.send_enter(rt.tmux, session_id),
            ):
                raise_for_tmux(
                    await rt.transport.run(argv, timeout=timeout), rt.config.tmux_path, host_id
                )
        self.invalidate(host_id)
        return await self.get(host_id, session_id)

    async def rename(self, host_id: str, session_id: str, new_name: str) -> TmuxSession:
        """Rename a session; its id ($N) and processes are unaffected."""
        rt = self.hosts.get(host_id)
        validate_session_name(new_name)
        session = await self.get(host_id, session_id)
        if session.name == new_name:
            return session
        timeout = rt.config.command_timeout
        exists = await rt.transport.run(tc.has_session(rt.tmux, new_name), timeout=timeout)
        if exists.exit_status == 0:
            raise MuxroomError(
                "session_exists", f"Session {new_name!r} already exists", host_id=host_id
            )
        result = await rt.transport.run(
            tc.rename_session(rt.tmux, session_id, new_name), timeout=timeout
        )
        raise_for_tmux(result, rt.config.tmux_path, host_id)
        self.invalidate(host_id)
        return await self.get(host_id, session_id)

    async def pane_cwd(self, host_id: str, session_id: str) -> str:
        rt = self.hosts.get(host_id)
        validate_session_id(session_id)
        result = await rt.transport.run(
            tc.pane_current_path(rt.tmux, session_id), timeout=rt.config.command_timeout
        )
        raise_for_tmux(result, rt.config.tmux_path, host_id)
        path = result.stdout.strip()
        if not path.startswith("/"):
            raise MuxroomError(
                "tmux_error", "Could not determine the pane directory", host_id=host_id
            )
        if "\\$" in path:
            # tmux <= 3.4 prints '$' in paths as '\$'; use whichever spelling exists.
            raw, unescaped = await self.hosts.realpath_dirs(
                host_id, [path, path.replace("\\$", "$")]
            )
            path = raw or unescaped or path
        return path


def _split_sections(stdout: str) -> dict[str, str]:
    """Split overview output on marker lines (`@@muxroom@@<name>[=value]`)."""
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in stdout.splitlines():
        if line.startswith(_MARK):
            if current is not None:
                sections[current] = "\n".join(lines)
            name, _, value = line[len(_MARK) :].partition("=")
            current, lines = name, ([value] if value else [])
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines)
    return sections
