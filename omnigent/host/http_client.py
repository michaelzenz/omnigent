"""Shared HTTP client helpers for the host daemon."""

from __future__ import annotations

import logging

from omnigent.chat import _remote_headers
from omnigent.host.identity import HOST_ID_HEADER

_logger = logging.getLogger(__name__)


def build_host_http_headers(server_url: str, *, host_id: str) -> dict[str, str]:
    """Build Omnigent HTTP headers for host-side pollers and bridges."""
    headers = dict(_remote_headers(server_url=server_url))
    headers[HOST_ID_HEADER] = host_id
    if "Authorization" in headers:
        return headers
    try:
        from omnigent.runner._entry import _make_auth_token_factory

        factory = _make_auth_token_factory(server_url=server_url)
        token = factory() if factory else None
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:  # noqa: BLE001
        _logger.debug("Could not obtain auth token for host HTTP client", exc_info=True)
    return headers
