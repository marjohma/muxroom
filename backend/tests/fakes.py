"""Deterministic fakes for transports, terminals and tmux."""

import asyncio
import posixpath
from collections.abc import Sequence
from dataclasses import dataclass, field

from muxroom.errors import MuxroomError
from muxroom.transport.base import CommandResult


@dataclass
class FakeSession:
    id: str
    name: str
    cwd: str
    keys: list[str] = field(default_factory=list)
    created: int = 1_700_000_000


class FakeTerminal:
    def __init__(self, *, echo: bool = False) -> None:
        self.echo = echo
        self.output: asyncio.Queue[bytes] = asyncio.Queue()
        self.written: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []
        self.close_calls = 0

    async def read(self) -> bytes:
        return await self.output.get()

    async def write(self, data: bytes) -> None:
        self.written.append(data)
        if self.echo:
            self.output.put_nowait(data)

    async def resize(self, cols: int, rows: int) -> None:
        self.sizes.append((cols, rows))

    async def close(self) -> None:
        self.close_calls += 1
        self.output.put_nowait(b"")


class FakeTmuxTransport:
    """Simulates a host with a POSIX shell and a tmux server.

    Only understands the exact argv shapes muxroom produces, so tests
    also pin the command construction.
    """

    def __init__(
        self,
        host_id: str = "local",
        *,
        home: str = "/home/u",
        dirs: Sequence[str] = ("/home/u", "/home/u/proj", "/data"),
        symlinks: dict[str, str] | None = None,
        shell: str = "/bin/bash",
        tmux_installed: bool = True,
        error: MuxroomError | None = None,
        delay: float = 0.0,
        echo_terminals: bool = True,
        files=None,
        agent_panes: dict[str, tuple[str, int]] | None = None,
    ) -> None:
        # session id -> (foreground command line, seconds since last output)
        self.agent_panes = agent_panes or {}
        self.echo_terminals = echo_terminals
        self._files = files
        self.host_id = host_id
        self.home = home
        self.dirs = set(dirs)
        self.symlinks = symlinks or {}
        self.shell = shell
        self.tmux_installed = tmux_installed
        self.error = error
        self.delay = delay
        self.sessions: dict[str, FakeSession] = {}
        self.calls: list[list[str]] = []
        self.terminals: list[tuple[list[str], FakeTerminal]] = []
        self.closed = False
        self._next_id = 0

    def add_session(self, name: str, cwd: str = "/home/u") -> FakeSession:
        sid = f"${self._next_id}"
        self._next_id += 1
        session = FakeSession(sid, name, cwd)
        self.sessions[sid] = session
        return session

    def _realdir(self, path: str) -> str | None:
        path = posixpath.normpath(path)
        for link, target in self.symlinks.items():
            if path == link or path.startswith(link + "/"):
                path = posixpath.normpath(target + path[len(link) :])
        return path if path in self.dirs else None

    def _find(self, target: str) -> FakeSession | None:
        if target.startswith("="):
            return next((s for s in self.sessions.values() if s.name == target[1:]), None)
        return self.sessions.get(target)

    async def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        argv = list(argv)
        self.calls.append(argv)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        if argv[:2] == ["sh", "-c"]:
            return self._sh(argv[2], argv[4:])
        if not self.tmux_installed:
            return CommandResult(127, "", f"sh: {argv[0]}: command not found")
        return self._tmux(argv[1:])

    NOW = 1_800_000_000

    def _overview(self) -> CommandResult:
        mark = "@@muxroom@@"
        if not self.tmux_installed:
            return CommandResult(0, f"{mark}rc=127\nsh: 1: tmux: not found\n{mark}panes\n", "")
        listing = self._tmux(["list-sessions"])
        text = listing.stdout if listing.exit_status == 0 else listing.stderr + "\n"
        panes, procs = [], []
        for i, (sid, (args, silent)) in enumerate(self.agent_panes.items()):
            panes.append(f"{sid}\t%{i}\t/dev/pts/{i}\t{self.NOW - silent}")
            procs.append(f"pts/{i} S+ {args}")
        out = (
            f"{mark}rc={listing.exit_status}\n{text}{mark}panes\n"
            + "".join(p + "\n" for p in panes)
            + f"{mark}now\n{self.NOW}\n{mark}ps\n"
            + "".join(p + "\n" for p in procs)
        )
        return CommandResult(0, out, "")

    def _sh(self, script: str, args: list[str]) -> CommandResult:
        if script.startswith("t=$1; sf=$2"):
            return self._overview()
        if script.startswith('printf "home='):
            tmux = "tmux=tmux 3.5a" if self.tmux_installed else "tmux_missing"
            return CommandResult(0, f"home={self.home}\nshell={self.shell}\n{tmux}\n", "")
        if script.startswith("for p in /opt/homebrew"):
            return CommandResult(0, "/opt/homebrew/bin/tmux\n", "")
        if script.startswith("for d; do"):
            return CommandResult(0, "".join((self._realdir(a) or "") + "\n" for a in args), "")
        raise AssertionError(f"unexpected script: {script}")

    def _tmux(self, args: list[str]) -> CommandResult:
        if args and args[0] == "-L":
            args = args[2:]
        cmd, rest = args[0], args[1:]
        if cmd == "list-sessions":
            if not self.sessions:
                return CommandResult(1, "", "no server running on /tmp/tmux-1/default")
            lines = [
                f"{s.id}\t{s.created}\t0\t1\t{s.created}\t{s.cwd}\t{s.name}"
                for s in self.sessions.values()
            ]
            return CommandResult(0, "\n".join(lines) + "\n", "")
        if cmd == "has-session":
            found = self._find(rest[rest.index("-t") + 1])
            return CommandResult(0 if found else 1, "", "" if found else "can't find session")
        if cmd == "new-session":
            name = rest[rest.index("-s") + 1]
            if self._find(f"={name}"):
                return CommandResult(1, "", f"duplicate session: {name}")
            session = self.add_session(name, rest[rest.index("-c") + 1])
            return CommandResult(0, session.id + "\n", "")
        if cmd == "send-keys":
            session = self._find(rest[rest.index("-t") + 1])
            if not session:
                return CommandResult(1, "", "can't find session")
            session.keys.append(rest[-1])
            return CommandResult(0, "", "")
        if cmd == "rename-session":
            session = self._find(rest[rest.index("-t") + 1])
            if not session:
                return CommandResult(1, "", "can't find session")
            if self._find(f"={rest[-1]}"):
                return CommandResult(1, "", f"duplicate session: {rest[-1]}")
            session.name = rest[-1]
            return CommandResult(0, "", "")
        if cmd == "display-message":
            session = self._find(rest[rest.index("-t") + 1])
            if not session:
                return CommandResult(1, "", "can't find session")
            return CommandResult(0, session.cwd + "\n", "")
        raise AssertionError(f"unexpected tmux command: {args}")

    async def open_terminal(self, argv: Sequence[str], *, cols: int, rows: int) -> FakeTerminal:
        if self.error:
            raise self.error
        terminal = FakeTerminal(echo=self.echo_terminals)
        terminal.sizes.append((cols, rows))
        self.terminals.append((list(argv), terminal))
        return terminal

    def files(self):
        if self._files is None:
            raise AssertionError("no file transport configured for this fake")
        return self._files

    async def close(self) -> None:
        self.closed = True
