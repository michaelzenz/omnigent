"""Shared context for host ambient pollers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from omnigent.host.identity import CONFIG_PATH

_POST_TIMEOUT_S = 30.0


@dataclass
class PollContext:
    """Dependencies shared by every registered poller."""

    server_url: str
    host_id: str
    client: httpx.AsyncClient
    config_path: Path = CONFIG_PATH


def build_poll_http_client(server_url: str, *, host_id: str) -> httpx.AsyncClient:
    """Build the shared Omnigent HTTP client for host pollers."""
    from omnigent.host.codex_ambient_bridge import _build_http_headers

    headers = _build_http_headers(server_url, host_id=host_id)
    timeout = httpx.Timeout(_POST_TIMEOUT_S)
    return httpx.AsyncClient(
        base_url=server_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
    )
