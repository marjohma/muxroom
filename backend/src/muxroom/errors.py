"""Error hierarchy with stable machine-readable codes shared by API and UI."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    status: int
    hint: str


ERROR_SPECS: dict[str, ErrorSpec] = {
    "unauthorized": ErrorSpec(401, "Open the URL printed by `muxroom` on startup."),
    "forbidden_origin": ErrorSpec(403, "Requests must come from the muxroom UI itself."),
    "bad_host_header": ErrorSpec(400, "Access muxroom via 127.0.0.1 or localhost."),
    "host_not_found": ErrorSpec(404, "Check the host id against your config.toml."),
    "session_not_found": ErrorSpec(404, "The tmux session may have ended. Refresh the list."),
    "session_exists": ErrorSpec(409, "Choose a different session name."),
    "invalid_name": ErrorSpec(422, "Use 1-64 characters: letters, digits, '-' and '_'."),
    "invalid_path": ErrorSpec(422, "Use an absolute path or one starting with '~/'."),
    "invalid_request": ErrorSpec(422, "Check the request parameters."),
    "path_not_allowed": ErrorSpec(
        422, "Sessions may only start inside the host's allowed_roots (config.toml)."
    ),
    "path_not_found": ErrorSpec(422, "The directory does not exist on the host."),
    "agent_not_allowed": ErrorSpec(422, "Only agents configured for this host can be launched."),
    "host_unreachable": ErrorSpec(502, "Check VPN / network connectivity and the ssh_host alias."),
    "ssh_auth_failed": ErrorSpec(
        502,
        "muxroom never prompts for passwords. Use 'Sign in' for this host, or make "
        "non-interactive auth work (ssh-agent key, ControlMaster, Kerberos).",
    ),
    "ssh_host_key_unknown": ErrorSpec(
        502, "Run `ssh <host>` once in a terminal to verify and store the host key."
    ),
    "ssh_host_key_mismatch": ErrorSpec(
        502, "The host key changed. Verify it out of band, then fix ~/.ssh/known_hosts."
    ),
    "ssh_error": ErrorSpec(502, "Run the same host with `ssh -v <host>` to see details."),
    "tmux_not_found": ErrorSpec(
        502, "tmux is not on the non-interactive PATH. Set tmux_path for this host."
    ),
    "tmux_error": ErrorSpec(502, "tmux reported an error; see the message for details."),
    "timeout": ErrorSpec(504, "The host did not answer in time. Check connectivity."),
    "path_outside_roots": ErrorSpec(
        403, "File access is limited to the host's read_roots / upload_roots."
    ),
    "permission_denied": ErrorSpec(403, "The remote user lacks permission for this path."),
    "upload_not_allowed": ErrorSpec(
        403, "Uploads are only allowed inside the host's upload_roots (config.toml)."
    ),
    "file_not_found": ErrorSpec(404, "The file or directory does not exist."),
    "file_exists": ErrorSpec(409, "A file with that name exists. Enable overwrite to replace it."),
    "file_too_large": ErrorSpec(413, "Raise the limit in the [files] section of config.toml."),
    "file_changed": ErrorSpec(409, "The file changed while it was read. Try again."),
    "preview_unsupported": ErrorSpec(415, "Only PDF, PNG, JPEG and SVG can be previewed."),
    "not_a_directory": ErrorSpec(422, "The path is not a directory."),
    "not_a_file": ErrorSpec(422, "The path is not a regular file."),
    "invalid_config": ErrorSpec(
        422, "Fix the configuration file; the previous configuration stays active."
    ),
    "host_exists": ErrorSpec(409, "Choose a different host id."),
    "host_editing_disabled": ErrorSpec(
        403, "Adding hosts from the UI is disabled (server.allow_host_editing in config.toml)."
    ),
    "unknown_ssh_alias": ErrorSpec(
        422, "Only Host aliases defined in ~/.ssh/config can be added from the UI."
    ),
    "connection_test_failed": ErrorSpec(
        422, "Fix the connection problem shown by the test, then try again."
    ),
    "unknown_endpoint": ErrorSpec(
        404,
        "If you just updated muxroom, the browser UI may be newer than the running "
        "backend: restart muxroom (your tmux sessions keep running).",
    ),
    "sftp_unavailable": ErrorSpec(
        502, "The host's SFTP subsystem is unavailable; file features need it."
    ),
}


class MuxroomError(Exception):
    """An expected, user-facing failure with a stable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        host_id: str | None = None,
        hint: str | None = None,
    ) -> None:
        if code not in ERROR_SPECS:
            raise ValueError(f"unknown error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.host_id = host_id
        self.hint = hint if hint is not None else ERROR_SPECS[code].hint

    @property
    def status(self) -> int:
        return ERROR_SPECS[self.code].status

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "host_id": self.host_id,
        }
