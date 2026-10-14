"""Parsing of tmux output and classification of tmux failures."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from muxroom.errors import MuxroomError
from muxroom.tmux.commands import FIELD_SEP, SESSION_FIELDS
from muxroom.transport.base import CommandResult

_VERSION_RE = re.compile(r"(\d+)\.(\d+)")
_NO_SERVER_MARKERS = ("no server running", "error connecting to")
_NOT_FOUND_MARKERS = ("command not found", "not found", "no such file or directory")


@dataclass(frozen=True, slots=True)
class TmuxSession:
    id: str
    name: str
    created: datetime
    last_activity: datetime
    attached_clients: int
    windows: int
    path: str


def _timestamp(value: str) -> datetime:
    return datetime.fromtimestamp(int(value), tz=UTC)


def parse_sessions(stdout: str) -> list[TmuxSession]:
    """Parse `list-sessions -F SESSION_FORMAT` output; skip malformed lines."""
    sessions: list[TmuxSession] = []
    for line in stdout.splitlines():
        parts = line.split(FIELD_SEP, len(SESSION_FIELDS) - 1)
        if len(parts) != len(SESSION_FIELDS):
            continue
        sid, created, attached, windows, activity, path, name = parts
        try:
            sessions.append(
                TmuxSession(
                    id=sid,
                    name=name,
                    created=_timestamp(created),
                    last_activity=_timestamp(activity or created),
                    attached_clients=int(attached or 0),
                    windows=int(windows),
                    path=path,
                )
            )
        except ValueError:
            continue
    return sessions


def parse_version(stdout: str) -> tuple[int, int] | None:
    match = _VERSION_RE.search(stdout)
    return (int(match[1]), int(match[2])) if match else None


def is_no_server(result: CommandResult) -> bool:
    stderr = result.stderr.lower()
    return result.exit_status != 0 and any(m in stderr for m in _NO_SERVER_MARKERS)


def is_tmux_missing(result: CommandResult, tmux_path: str) -> bool:
    if result.exit_status == 127:
        return True
    stderr = result.stderr.lower()
    return (
        result.exit_status != 0
        and tmux_path.lower() in stderr
        and any(m in stderr for m in _NOT_FOUND_MARKERS)
        and not any(m in stderr for m in _NO_SERVER_MARKERS)
    )


def raise_for_tmux(result: CommandResult, tmux_path: str, host_id: str) -> None:
    """Translate a failed tmux invocation into an MuxroomError."""
    if result.exit_status == 0:
        return
    stderr = result.stderr.strip()
    if is_tmux_missing(result, tmux_path):
        raise MuxroomError(
            "tmux_not_found", f"tmux not found at {tmux_path!r} on {host_id}", host_id=host_id
        )
    lowered = stderr.lower()
    if "duplicate session" in lowered:
        raise MuxroomError("session_exists", stderr, host_id=host_id)
    if "can't find session" in lowered or "session not found" in lowered:
        raise MuxroomError("session_not_found", stderr, host_id=host_id)
    raise MuxroomError(
        "tmux_error", stderr or f"tmux exited with {result.exit_status}", host_id=host_id
    )
