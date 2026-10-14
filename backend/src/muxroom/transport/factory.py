"""Build the transport for a configured host."""

from muxroom.config import HostConfig
from muxroom.transport.base import HostTransport
from muxroom.transport.local import LocalTransport


def create_transport(host: HostConfig) -> HostTransport:
    if host.transport == "local":
        return LocalTransport(host.id)
    if host.transport == "openssh":
        from muxroom.transport.openssh import OpenSshTransport

        return OpenSshTransport(host)
    from muxroom.transport.asyncssh_transport import AsyncSshTransport

    return AsyncSshTransport(host)
