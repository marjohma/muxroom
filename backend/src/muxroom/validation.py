"""Syntactic validation of identifiers and paths received from clients."""

import posixpath
import re

from muxroom.errors import MuxroomError

HOST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SESSION_ID_RE = re.compile(r"^\$\d{1,9}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

MAX_PATH_BYTES = 1024
MAX_FILENAME_BYTES = 255
UPLOAD_TEMP_PREFIX = ".muxroom-upload-"

MIN_COLS, MAX_COLS = 2, 1000
MIN_ROWS, MAX_ROWS = 2, 500


def is_valid_host_id(value: str) -> bool:
    return bool(HOST_ID_RE.fullmatch(value))


def validate_session_name(name: str) -> str:
    if not SESSION_NAME_RE.fullmatch(name):
        raise MuxroomError("invalid_name", f"Invalid session name: {name!r}")
    return name


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_RE.fullmatch(session_id):
        raise MuxroomError("session_not_found", f"Invalid session id: {session_id!r}")
    return session_id


def _check_path_chars(path: str) -> None:
    if not path:
        raise MuxroomError("invalid_path", "Path must not be empty")
    if len(path.encode()) > MAX_PATH_BYTES:
        raise MuxroomError("invalid_path", "Path is too long")
    if CONTROL_CHARS_RE.search(path):
        raise MuxroomError("invalid_path", "Path contains control characters")


def validate_user_path(path: str) -> str:
    """Accept absolute paths and `~` / `~/...`; reject everything else."""
    _check_path_chars(path)
    if not (path.startswith("/") or path == "~" or path.startswith("~/")):
        raise MuxroomError("invalid_path", f"Path must be absolute or start with '~/': {path!r}")
    return path


def validate_relative_path(path: str) -> str:
    _check_path_chars(path)
    if path.startswith(("/", "~")):
        raise MuxroomError("invalid_path", "Path must be relative to the session directory")
    return path


def expand_home(path: str, home: str) -> str:
    """Expand a leading `~` with the host's home directory and normalize."""
    if path == "~":
        path = home
    elif path.startswith("~/"):
        path = posixpath.join(home, path[2:])
    return posixpath.normpath(path)


def validate_filename(name: str) -> str:
    if (
        not name
        or len(name.encode()) > MAX_FILENAME_BYTES
        or "/" in name
        or name in (".", "..")
        or CONTROL_CHARS_RE.search(name)
        or name.startswith(UPLOAD_TEMP_PREFIX)
    ):
        raise MuxroomError("invalid_name", f"Invalid file name: {name!r}")
    return name


def validate_terminal_size(cols: int, rows: int) -> tuple[int, int]:
    if not (MIN_COLS <= cols <= MAX_COLS and MIN_ROWS <= rows <= MAX_ROWS):
        raise MuxroomError("invalid_request", f"Invalid terminal size {cols}x{rows}")
    return cols, rows


def is_within(path: str, root: str) -> bool:
    """True if normalized absolute `path` equals or lies below `root`."""
    return posixpath.commonpath([root, path]) == root
