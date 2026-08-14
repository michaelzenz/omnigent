"""Tests for optional server Unix-socket transport selection."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from omnigent import server_transport


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
