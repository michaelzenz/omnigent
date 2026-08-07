"""Tests for SSH settings routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

from omnigent.ssh_connections_store import SshConnectionProfile
from omnigent.ssh_probe import SshProbeResult


async def test_ssh_test_route_returns_probe_result(client: httpx.AsyncClient) -> None:
    with patch(
        "omnigent.server.routes.ssh_connections.probe_ssh",
        new=AsyncMock(return_value=SshProbeResult(ok=True, message="Connected", latency_ms=42)),
    ):
        resp = await client.post(
            "/v1/ssh/test",
            json={"alias": "arca.ssh"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "message": "Connected", "latency_ms": 42}


async def test_put_ssh_connections_persists_profiles(client: httpx.AsyncClient) -> None:
    stored: list[SshConnectionProfile] = []

    def _write(profiles: list[SshConnectionProfile]) -> None:
        stored.clear()
        stored.extend(profiles)

    with patch(
        "omnigent.server.routes.ssh_connections.read_ssh_connections",
        return_value=[],
    ):
        with patch(
            "omnigent.server.routes.ssh_connections.write_ssh_connections",
            side_effect=_write,
        ):
            resp = await client.put(
                "/v1/ssh/connections",
                json={"connections": [{"label": "Arca", "alias": "arca.ssh"}]},
            )
    assert resp.status_code == 200
    assert len(stored) == 1
    assert stored[0].alias == "arca.ssh"
    assert stored[0].label == "Arca"


async def test_put_ssh_connections_keeps_created_at_of_existing_profile(
    client: httpx.AsyncClient,
) -> None:
    """Re-saving a profile must not reset when it was first added."""
    stored: list[SshConnectionProfile] = []

    def _write(profiles: list[SshConnectionProfile]) -> None:
        stored.clear()
        stored.extend(profiles)

    existing = SshConnectionProfile(
        id="profile-1",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
    )
    with patch(
        "omnigent.server.routes.ssh_connections.read_ssh_connections",
        return_value=[existing],
    ):
        with patch(
            "omnigent.server.routes.ssh_connections.write_ssh_connections",
            side_effect=_write,
        ):
            resp = await client.put(
                "/v1/ssh/connections",
                json={
                    "connections": [{"id": "profile-1", "label": "Arca II", "alias": "arca.ssh"}]
                },
            )
    assert resp.status_code == 200
    assert stored[0].created_at == "2026-01-01T00:00:00+00:00"
    assert stored[0].label == "Arca II"
