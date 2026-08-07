"""Tests for host poll and timer HTTP client transport selection."""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.host.polling import context


def test_poll_client_uses_optional_server_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll clients pass the selected UDS transport to httpx."""
    transport = object()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        context,
        "server_async_http_transport_kwargs",
        lambda: {"transport": transport},
    )
    monkeypatch.setattr(
        context.httpx,
        "AsyncClient",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    context.build_poll_http_client("https://server.example.com/", host_id="host_123")

    assert captured["base_url"] == "https://server.example.com"
    assert captured["transport"] is transport


def test_poll_client_uses_default_transport_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset UDS configuration retains httpx's normal transport selection."""
    captured: dict[str, Any] = {}

    monkeypatch.setattr(context, "server_async_http_transport_kwargs", dict)
    monkeypatch.setattr(
        context.httpx,
        "AsyncClient",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    context.build_poll_http_client("https://server.example.com", host_id="host_123")

    assert "transport" not in captured
