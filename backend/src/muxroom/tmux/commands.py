"""tmux argv builders. Pure functions; nothing here performs I/O.

Every builder returns an argv list. Transports either exec it directly
(local) or join it with `shlex.join` for a remote shell (SSH).
"""

from dataclasses import dataclass

FIELD_SEP = "\t"
SESSION_FIELDS = (
    "#{session_id}",
    "#{session_created}",
    "#{session_attached}",
    "#{session_windows}",
    "#{session_activity}",
    "#{session_path}",
    "#{session_name}",  # last: names may contain the separator
)
SESSION_FORMAT = FIELD_SEP.join(SESSION_FIELDS)


@dataclass(frozen=True, slots=True)
class TmuxTarget:
    """Which tmux binary and server (socket) to talk to."""

    tmux_path: str = "tmux"
    socket_name: str | None = None

    def base(self, *, utf8: bool = False) -> list[str]:
        argv = [self.tmux_path]
        if utf8:
            argv.append("-u")
        if self.socket_name:
            argv += ["-L", self.socket_name]
        return argv


def version(t: TmuxTarget) -> list[str]:
    return [t.tmux_path, "-V"]


def list_sessions(t: TmuxTarget) -> list[str]:
    return [*t.base(), "list-sessions", "-F", SESSION_FORMAT]


def has_session(t: TmuxTarget, name: str) -> list[str]:
    return [*t.base(), "has-session", "-t", f"={name}"]


def new_session(t: TmuxTarget, name: str, cwd: str) -> list[str]:
    """Create a detached session running the user's default shell.

    tmux expands formats in -c (a directory named `#{pid}` would be replaced),
    so '#' is escaped as '##'.
    """
    return [
        *t.base(),
        "new-session",
        "-d",
        "-s",
        name,
        "-c",
        cwd.replace("#", "##"),
        "-P",
        "-F",
        "#{session_id}",
    ]


def send_literal(t: TmuxTarget, session_id: str, text: str) -> list[str]:
    return [*t.base(), "send-keys", "-t", session_id, "-l", text]


def send_enter(t: TmuxTarget, session_id: str) -> list[str]:
    return [*t.base(), "send-keys", "-t", session_id, "Enter"]


def rename_session(t: TmuxTarget, session_id: str, new_name: str) -> list[str]:
    return [*t.base(), "rename-session", "-t", session_id, new_name]


def attach(t: TmuxTarget, session_id: str) -> list[str]:
    """Attach as an additional client. Never `-d`: other clients stay attached."""
    return [*t.base(utf8=True), "attach-session", "-t", session_id]


def pane_current_path(t: TmuxTarget, session_id: str) -> list[str]:
    return [*t.base(), "display-message", "-p", "-t", session_id, "#{pane_current_path}"]
