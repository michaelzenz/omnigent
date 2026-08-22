"""Optional Unix-domain-socket transport for server connections."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    import httpx

OMNIGENT_SERVER_UNIX_SOCKET = "OMNIGENT_SERVER_UNIX_SOCKET"


class AsyncServerTransportKwargs(TypedDict, total=False):
    """Optional keyword arguments for ``httpx.AsyncClient``."""

    transport: httpx.AsyncHTTPTransport


class ServerTransportKwargs(TypedDict, total=False):
    """Optional keyword arguments for ``httpx.Client``."""

    transport: httpx.HTTPTransport


def server_unix_socket_path(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the expanded server socket path, or ``None`` when disabled."""
    source = os.environ if environ is None else environ
    value = source.get(OMNIGENT_SERVER_UNIX_SOCKET)
    if value is None or not value.strip():
        return None
    return str(Path(value.strip()).expanduser())


def server_async_http_transport_kwargs() -> AsyncServerTransportKwargs:
    """Return async client kwargs without overriding defaults when disabled."""
    import httpx

    socket_path = server_unix_socket_path()
    if socket_path is None:
        return {}
    return {"transport": httpx.AsyncHTTPTransport(uds=socket_path)}


def server_http_transport_kwargs() -> ServerTransportKwargs:
    """Return sync client kwargs without overriding defaults when disabled."""
    import httpx

    socket_path = server_unix_socket_path()
    if socket_path is None:
        return {}
    return {"transport": httpx.HTTPTransport(uds=socket_path)}
