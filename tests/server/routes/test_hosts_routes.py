"""Tests for the hosts REST routes (``/v1/hosts``).

The standard test ``app`` fixture supplies a ``HostStore``, so the hosts
router is always mounted; an empty store lists zero hosts and lookups of
unknown ids 404.
"""

from __future__ import annotations

import httpx


async def test_hosts_lists_empty_without_registered_hosts(
    client: httpx.AsyncClient,
) -> None:
    """GET /v1/hosts returns an empty list when no host has connected."""
    resp = await client.get("/v1/hosts")
    assert resp.status_code == 200
    assert resp.json()["hosts"] == []


async def test_get_host_unknown_id_404s(client: httpx.AsyncClient) -> None:
    """GET /v1/hosts/{id} returns 404 for a host that never connected."""
    resp = await client.get("/v1/hosts/host_nonexistent_12345")
    assert resp.status_code == 404
