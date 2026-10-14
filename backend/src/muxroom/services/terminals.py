"""Bridges browser WebSockets to tmux client terminals.

The only cleanup action ever taken is `TerminalConnection.close()`, which
detaches this one tmux client. Sessions and other clients are untouched.
"""

import asyncio
import contextlib
import json
import logging
from typing import Any, Protocol

from muxroom.errors import MuxroomError
from muxroom.services.hosts import HostService
from muxroom.services.sessions import SessionService
from muxroom.tmux import commands as tc
from muxroom.transport.base import TerminalConnection
from muxroom.transport.openssh import OpenSshTransport
from muxroom.transport.process import PtyTerminal
from muxroom.validation import validate_session_id, validate_terminal_size

log = logging.getLogger(__name__)

MAX_INPUT_FRAME = 64 * 1024
MAX_CONTROL_FRAME = 4 * 1024


class ClientDisconnected(Exception):
    pass


class TerminalSocket(Protocol):
    """The subset of a WebSocket the bridge needs (eases testing)."""

    async def send_bytes(self, data: bytes) -> None: ...
    async def send_text(self, data: str) -> None: ...
    async def receive(self) -> dict[str, Any]: ...


class TerminalService:
    def __init__(self, hosts: HostService, sessions: SessionService) -> None:
        self.hosts = hosts
        self.sessions = sessions
        self._open: set[TerminalConnection] = set()

    @property
    def open_count(self) -> int:
        return len(self._open)

    async def open(self, host_id: str, session_id: str, cols: int, rows: int) -> TerminalConnection:
        validate_session_id(session_id)
        validate_terminal_size(cols, rows)
        await self.sessions.get(host_id, session_id)
        rt = self.hosts.get(host_id)
        terminal = await rt.transport.open_terminal(
            tc.attach(rt.tmux, session_id), cols=cols, rows=rows
        )
        self._open.add(terminal)
        return terminal

    async def bridge(
        self, host_id: str, session_id: str, socket: TerminalSocket, terminal: TerminalConnection
    ) -> None:
        """Pump bytes both ways until either side ends, then detach."""
        output = asyncio.create_task(self._pump_output(terminal, socket))
        incoming = asyncio.create_task(self._pump_input(socket, terminal))
        try:
            done, _ = await asyncio.wait({output, incoming}, return_when=asyncio.FIRST_COMPLETED)
            if output in done and not output.exception():
                reason = await self._exit_reason(host_id, session_id)
                with contextlib.suppress(Exception):
                    await socket.send_text(json.dumps({"type": "exit", "reason": reason}))
        finally:
            for task in (output, incoming):
                task.cancel()
            for task in (output, incoming):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self.release(terminal)

    async def release(self, terminal: TerminalConnection) -> None:
        self._open.discard(terminal)
        try:
            await terminal.close()
        except Exception:
            log.exception("closing terminal failed")

    async def _pump_output(self, terminal: TerminalConnection, socket: TerminalSocket) -> None:
        while chunk := await terminal.read():
            await socket.send_bytes(chunk)

    async def _pump_input(self, socket: TerminalSocket, terminal: TerminalConnection) -> None:
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                raise ClientDisconnected
            if (data := message.get("bytes")) is not None:
                if len(data) > MAX_INPUT_FRAME:
                    await socket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "code": "invalid_request",
                                "message": "Frame too large",
                            }
                        )
                    )
                    continue
                await terminal.write(data)
            elif (text := message.get("text")) is not None:
                if len(text) > MAX_CONTROL_FRAME:
                    await socket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "code": "invalid_request",
                                "message": "Frame too large",
                            }
                        )
                    )
                    continue
                await self._handle_control(text, terminal, socket)

    async def _handle_control(
        self, text: str, terminal: TerminalConnection, socket: TerminalSocket
    ) -> None:
        try:
            payload = json.loads(text)
            if payload.get("type") != "resize":
                raise ValueError("unknown message type")
            cols, rows = validate_terminal_size(int(payload["cols"]), int(payload["rows"]))
        except (ValueError, KeyError, TypeError, AttributeError, MuxroomError) as exc:
            await socket.send_text(
                json.dumps({"type": "error", "code": "invalid_request", "message": str(exc)})
            )
            return
        await terminal.resize(cols, rows)

    async def _exit_reason(self, host_id: str, session_id: str) -> str:
        try:
            await asyncio.wait_for(self.sessions.get(host_id, session_id), 5)
        except MuxroomError as err:
            return "session_ended" if err.code == "session_not_found" else "transport_lost"
        except TimeoutError:
            return "transport_lost"
        return "detached"

    async def sign_in(self, host_id: str, socket: TerminalSocket, cols: int, rows: int) -> None:
        """Interactive `ssh -f -N` in a browser terminal to create a ControlMaster.

        The password goes straight from the browser terminal into ssh;
        muxroom neither parses nor stores it. ssh -f keeps the PTY open in
        its background process, so completion is detected by process exit.
        """
        rt = self.hosts.get(host_id)
        transport = rt.transport
        if not isinstance(transport, OpenSshTransport):
            raise MuxroomError(
                "invalid_request", "Sign in is only available for openssh hosts", host_id=host_id
            )
        resolved = await transport.resolved_config()
        if (
            resolved.get("controlmaster", "false") in ("false", "no")
            or resolved.get("controlpath", "none") == "none"
        ):
            raise MuxroomError(
                "invalid_request",
                f"No ControlMaster configured for {rt.config.ssh_host}",
                host_id=host_id,
                hint="Add ControlMaster auto, ControlPath and ControlPersist for this host in "
                "~/.ssh/config (see docs/remote-setup.md).",
            )
        validate_terminal_size(cols, rows)
        terminal = await PtyTerminal.spawn(transport.sign_in_argv(), cols=cols, rows=rows)
        self._open.add(terminal)
        output = asyncio.create_task(self._pump_output(terminal, socket))
        incoming = asyncio.create_task(self._pump_input(socket, terminal))
        exited = asyncio.create_task(terminal.wait_exit())
        try:
            done, _ = await asyncio.wait({exited, incoming}, return_when=asyncio.FIRST_COMPLETED)
            if exited in done:
                await asyncio.sleep(0.2)  # flush the last output
                ok = exited.result() == 0
                with contextlib.suppress(Exception):
                    await socket.send_text(
                        json.dumps(
                            {"type": "exit", "reason": "signed_in" if ok else "sign_in_failed"}
                        )
                    )
        finally:
            for task in (output, incoming, exited):
                task.cancel()
            await self.release(terminal)

    async def detach_all(self) -> None:
        for terminal in list(self._open):
            await self.release(terminal)
