"""Shared context for host timer handlers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.context import build_poll_http_client


@dataclass
class TimerContext:
    """Dependencies shared by every timer handler."""

    server_url: str
    host_id: str
    client: httpx.AsyncClient
    config_path: Path = CONFIG_PATH


def build_timer_http_client(server_url: str, *, host_id: str) -> httpx.AsyncClient:
    """Build the shared Omnigent HTTP client for host timer work."""
    return build_poll_http_client(server_url, host_id=host_id)
