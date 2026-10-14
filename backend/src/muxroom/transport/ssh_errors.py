"""Map OpenSSH / AsyncSSH failures to user-facing error codes."""

import socket

import asyncssh

from muxroom.errors import MuxroomError

SSH_EXIT_ERROR = 255

_OPENSSH_PATTERNS: list[tuple[str, str]] = [
    ("remote host identification has changed", "ssh_host_key_mismatch"),
    ("host key verification failed", "ssh_host_key_unknown"),
    ("no matching host key", "ssh_host_key_unknown"),
    ("permission denied", "ssh_auth_failed"),
    ("too many authentication failures", "ssh_auth_failed"),
    ("could not resolve hostname", "host_unreachable"),
    ("connection timed out", "host_unreachable"),
    ("operation timed out", "host_unreachable"),
    ("connection refused", "host_unreachable"),
    ("no route to host", "host_unreachable"),
    ("network is unreachable", "host_unreachable"),
    ("connection closed by", "host_unreachable"),
    ("connection reset", "host_unreachable"),
    ("broken pipe", "host_unreachable"),
    ("subsystem request failed", "sftp_unavailable"),
]


def classify_openssh(stderr: str, host_id: str) -> MuxroomError:
    lowered = stderr.lower()
    message = _last_meaningful_line(stderr) or "ssh failed"
    for needle, code in _OPENSSH_PATTERNS:
        if needle in lowered:
            return MuxroomError(code, message, host_id=host_id)
    return MuxroomError("ssh_error", message, host_id=host_id)


def _last_meaningful_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith(("@@@", "Warning: Permanently"))]
    return lines[-1] if lines else ""


def classify_asyncssh(exc: BaseException, host_id: str) -> MuxroomError:
    if isinstance(exc, MuxroomError):
        return exc
    message = str(exc) or type(exc).__name__
    if isinstance(exc, asyncssh.PermissionDenied):
        return MuxroomError("ssh_auth_failed", message, host_id=host_id)
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        code = "ssh_host_key_mismatch" if "mismatch" in message.lower() else "ssh_host_key_unknown"
        return MuxroomError(code, message, host_id=host_id)
    if isinstance(exc, TimeoutError):
        return MuxroomError("timeout", message, host_id=host_id)
    if isinstance(exc, (socket.gaierror, ConnectionError, OSError, asyncssh.ConnectionLost)):
        return MuxroomError("host_unreachable", message, host_id=host_id)
    if isinstance(exc, asyncssh.ChannelOpenError):
        return MuxroomError("ssh_error", message, host_id=host_id)
    return MuxroomError("ssh_error", message, host_id=host_id)
