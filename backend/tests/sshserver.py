"""In-process SSH server for transport tests.

Executes commands locally like sshd would (as the current user), supports
PTY sessions and the SFTP subsystem. Clients get a dedicated ssh_config,
key and known_hosts file, so the user's own SSH setup is never touched.
"""

import asyncio
import contextlib
import os
from dataclasses import dataclass
from pathlib import Path

import asyncssh

from muxroom.transport.process import PtyTerminal, child_env

ALIAS = "muxroom-test"


async def _run_with_pty(process: asyncssh.SSHServerProcess) -> None:
    cols, rows, *_ = process.get_terminal_size()
    term = await PtyTerminal.spawn(
        ["/bin/sh", "-c", process.command or "exec /bin/sh"], cols=cols or 80, rows=rows or 24
    )

    async def outbound() -> None:
        while data := await term.read():
            process.stdout.write(data)
        process.exit(0)

    async def inbound() -> None:
        while True:
            try:
                data = await process.stdin.read(65536)
            except asyncssh.TerminalSizeChanged as change:
                await term.resize(change.width, change.height)
                continue
            if not data:
                break
            await term.write(data)

    out_task = asyncio.create_task(outbound())
    in_task = asyncio.create_task(inbound())
    await asyncio.wait({out_task, in_task}, return_when=asyncio.FIRST_COMPLETED)
    in_task.cancel()
    await term.close()  # channel closed: hang up the program, like sshd's SIGHUP
    with contextlib.suppress(Exception):
        out_task.cancel()
    if not process.is_closing():
        process.exit(0)


async def _run_plain(process: asyncssh.SSHServerProcess) -> None:
    local = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        process.command or "true",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env(),
        start_new_session=True,
    )
    await process.redirect(stdin=local.stdin, stdout=local.stdout, stderr=local.stderr)
    process.exit(await local.wait())


async def _handle(process: asyncssh.SSHServerProcess) -> None:
    try:
        if process.get_terminal_type():
            await _run_with_pty(process)
        else:
            await _run_plain(process)
    except Exception as exc:  # pragma: no cover - surfaced through client failures
        import traceback

        traceback.print_exception(exc)
        process.exit(1)


@dataclass
class TestSshServer:
    port: int
    config_file: Path
    server: asyncssh.SSHAcceptor
    client_key: Path

    def close(self) -> None:
        self.server.close()


async def start_server(tmp: Path, *, authorize: bool = True, sftp: bool = True) -> TestSshServer:
    tmp.mkdir(parents=True, exist_ok=True)
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    key_path = tmp / "client_key"
    client_key.write_private_key(str(key_path))
    key_path.chmod(0o600)
    authorized = asyncssh.import_authorized_keys(
        client_key.export_public_key().decode() if authorize else ""
    )

    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=authorized,
        process_factory=_handle,
        sftp_factory=True if sftp else None,
        allow_scp=False,
        encoding=None,
    )
    port = server.sockets[0].getsockname()[1]
    known_hosts = tmp / "known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:{port} {host_key.export_public_key().decode().strip()}\n")
    config = tmp / "ssh_config"
    config.write_text(
        f"Host {ALIAS}\n"
        f"  HostName 127.0.0.1\n"
        f"  Port {port}\n"
        f"  User {os.environ.get('USER', 'user')}\n"
        f"  IdentityFile {key_path}\n"
        f"  IdentitiesOnly yes\n"
        f"  UserKnownHostsFile {known_hosts}\n"
        f"  GlobalKnownHostsFile /dev/null\n"
        f"  StrictHostKeyChecking yes\n"
        f"  ControlMaster no\n"
        f"  ControlPath none\n"
        f"  PasswordAuthentication no\n"
        f"  KbdInteractiveAuthentication no\n"
        f"  LogLevel ERROR\n"
    )
    return TestSshServer(port, config, server, key_path)
