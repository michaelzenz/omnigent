"""Tests for optional server Unix-socket transport selection."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnigent import server_transport
from omnigent.cli_auth import open_server_client


def test_server_unix_socket_path_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset and blank values leave the normal network transport enabled."""
    monkeypatch.delenv(server_transport.OMNIGENT_SERVER_UNIX_SOCKET, raising=False)
    assert server_transport.server_unix_socket_path() is None

    monkeypatch.setenv(server_transport.OMNIGENT_SERVER_UNIX_SOCKET, "  ")
    assert server_transport.server_unix_socket_path() is None


def test_server_unix_socket_path_expands_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Socket paths use the current user's home directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(server_transport.OMNIGENT_SERVER_UNIX_SOCKET, "~/server.sock")

    assert server_transport.server_unix_socket_path() == str(tmp_path / "server.sock")


def test_transport_kwargs_use_expanded_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Both sync and async HTTP clients receive the same expanded UDS path."""
    async_calls: list[str] = []
    sync_calls: list[str] = []
    async_sentinel = object()
    sync_sentinel = object()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(server_transport.OMNIGENT_SERVER_UNIX_SOCKET, "~/server.sock")
    monkeypatch.setattr(
        httpx,
        "AsyncHTTPTransport",
        lambda *, uds: async_calls.append(uds) or async_sentinel,
    )
    monkeypatch.setattr(
        httpx,
        "HTTPTransport",
        lambda *, uds: sync_calls.append(uds) or sync_sentinel,
    )

    assert server_transport.server_async_http_transport_kwargs() == {"transport": async_sentinel}
    assert server_transport.server_http_transport_kwargs() == {"transport": sync_sentinel}
    expected = str(tmp_path / "server.sock")
    assert async_calls == [expected]
    assert sync_calls == [expected]


def test_transport_kwargs_are_empty_when_socket_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the env var, clients keep httpx's default transport selection."""
    monkeypatch.delenv(server_transport.OMNIGENT_SERVER_UNIX_SOCKET, raising=False)

    assert server_transport.server_async_http_transport_kwargs() == {}
    assert server_transport.server_http_transport_kwargs() == {}


def _http_reply(body: bytes):
    """Return an asyncio stream handler answering one HTTP GET with *body*."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(65536)
        writer.write(
            b"HTTP/1.1 200 OK\r\ncontent-length: %d\r\nconnection: close\r\n\r\n%s"
            % (len(body), body)
        )
        await writer.drain()
        writer.close()

    return handle


async def test_open_server_client_dials_unix_socket_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the env var set, the client reaches the server through the socket."""
    # Keep the socket path short: AF_UNIX paths are capped (104 bytes on macOS)
    # and pytest tmp_path entries overflow the limit.
    sock_path = Path(tempfile.gettempdir()) / f"omnigent-uds-probe-{os.getpid()}.sock"
    sock_path.unlink(missing_ok=True)
    body = b"uds-ok"
    server = await asyncio.start_unix_server(_http_reply(body), path=str(sock_path))
    try:
        monkeypatch.setenv(server_transport.OMNIGENT_SERVER_UNIX_SOCKET, str(sock_path))
        client = open_server_client("http://localhost:6767", timeout=5.0)
        try:
            resp = await client.get("http://localhost:6767/probe")
        finally:
            await client.aclose()
        assert resp.status_code == 200
        assert resp.content == body
    finally:
        server.close()
        await server.wait_closed()
        sock_path.unlink(missing_ok=True)


async def test_open_server_client_keeps_tcp_when_socket_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the env var, the client still dials the server URL over TCP."""
    monkeypatch.delenv(server_transport.OMNIGENT_SERVER_UNIX_SOCKET, raising=False)
    body = b"tcp-ok"
    server = await asyncio.start_server(_http_reply(body), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = open_server_client(f"http://127.0.0.1:{port}", timeout=5.0)
        try:
            resp = await client.get(f"http://127.0.0.1:{port}/probe")
        finally:
            await client.aclose()
        assert resp.status_code == 200
        assert resp.content == body
    finally:
        server.close()
        await server.wait_closed()


async def test_open_server_client_builds_uds_transport_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The env socket path flows into httpx's UDS transport verbatim."""
    expected_socket = str(tmp_path / "server.sock")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(server_transport.OMNIGENT_SERVER_UNIX_SOCKET, "~/server.sock")
    real_transport = httpx.AsyncHTTPTransport
    seen_udss: list[str] = []

    def recording_transport(*, uds: str) -> httpx.AsyncHTTPTransport:
        seen_udss.append(uds)
        return real_transport(uds=uds)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", recording_transport)
    client = open_server_client("http://localhost:6767")
    try:
        assert seen_udss == [expected_socket]
    finally:
        await client.aclose()


async def test_open_server_client_explicit_transport_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-supplied transport is never overridden by the socket default."""
    monkeypatch.setenv(server_transport.OMNIGENT_SERVER_UNIX_SOCKET, "/nonexistent/probe.sock")
    mock = httpx.MockTransport(lambda request: httpx.Response(200))
    client = open_server_client("http://localhost:6767", transport=mock)
    try:
        assert client._transport is mock
    finally:
        await client.aclose()
