"""Runtime helper for reading the current disabled-tools set.

The runner imports :func:`get_disabled_tools` to filter schemas and
guard dispatch. The store lives on the server side; the runner reads
it via the server client. To avoid a network round-trip on every turn,
the runner caches the result and refreshes periodically.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

_logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30.0
_cached: frozenset[str] = frozenset()
_cached_at: float = 0.0
_cache_lock = asyncio.Lock()


async def refresh_disabled_tools(server_client: httpx.AsyncClient | None) -> frozenset[str]:
    """Fetch the current disabled-tools set from the server and cache it."""
    global _cached, _cached_at
    if server_client is None:
        return frozenset()
    try:
        resp = await server_client.get("/v1/tool-preferences", timeout=5.0)
        if resp.status_code != 200:
            return _cached
        body: dict[str, Any] = resp.json()
        names = body.get("disabled_tools")
        if isinstance(names, list):
            _cached = frozenset(names)
            _cached_at = time.time()
    except Exception:  # noqa: BLE001
        _logger.debug("Failed to fetch tool preferences", exc_info=True)
    return _cached


async def get_disabled_tools(server_client: httpx.AsyncClient | None) -> frozenset[str]:
    """Return the cached disabled-tools set, refreshing if stale."""
    global _cached_at
    if server_client is None:
        return frozenset()
    if time.time() - _cached_at > _CACHE_TTL_SECONDS:
        async with _cache_lock:
            if time.time() - _cached_at > _CACHE_TTL_SECONDS:
                await refresh_disabled_tools(server_client)
    return _cached


def get_disabled_tools_sync() -> frozenset[str]:
    """Return the last cached disabled-tools set (no network call).

    Used by synchronous code paths (e.g. Pi extension generation) that
    cannot await :func:`get_disabled_tools`. The cache is populated by
    the async path on each turn; if it has never been populated, returns
    an empty set (all tools enabled).
    """
    return _cached


def filter_tool_schemas(
    schemas: list[dict[str, Any]],
    disabled: frozenset[str],
) -> list[dict[str, Any]]:
    """Remove schemas whose tool name is in the disabled set."""
    if not disabled:
        return schemas
    result: list[dict[str, Any]] = []
    for schema in schemas:
        name = _extract_tool_name(schema)
        if name is not None and name in disabled:
            continue
        result.append(schema)
    return result


def _extract_tool_name(schema: dict[str, Any]) -> str | None:
    """Extract the tool name from an OpenAI-format schema dict."""
    name = schema.get("name")
    if isinstance(name, str):
        return name
    func = schema.get("function")
    if isinstance(func, dict):
        name = func.get("name")
        if isinstance(name, str):
            return name
    return None
