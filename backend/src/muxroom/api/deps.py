"""Application state container shared by routes."""

from dataclasses import dataclass

from starlette.requests import HTTPConnection

from muxroom.api.security import AccessPolicy
from muxroom.config import AppConfig
from muxroom.services.config_manager import ConfigManager
from muxroom.services.files import FileService
from muxroom.services.hosts import HostService
from muxroom.services.sessions import SessionService
from muxroom.services.terminals import TerminalService


@dataclass(slots=True)
class AppState:
    config: AppConfig
    policy: AccessPolicy
    hosts: HostService
    sessions: SessionService
    terminals: TerminalService
    files: FileService
    config_manager: ConfigManager


def state_of(conn: HTTPConnection) -> AppState:
    return conn.app.state.muxroom
