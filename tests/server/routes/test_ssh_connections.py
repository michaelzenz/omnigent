"""Tests for SSH settings routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from omnigent.server.routes.ssh_connections import create_ssh_connections_router
from omnigent.ssh_connections_store import (
    SshConnectionProfile,
    SshSettings,
)
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
            with patch(
                "omnigent.server.routes.ssh_connections.write_ssh_settings",
            ) as write_settings:
                resp = await client.put(
                    "/v1/ssh/connections",
                    json={"connections": [{"label": "Arca", "alias": "arca.ssh"}]},
                )
    assert resp.status_code == 200
    assert len(stored) == 1
    assert stored[0].alias == "arca.ssh"
    assert stored[0].label == "Arca"
    write_settings.assert_called_once_with(SshSettings(package_index_url=None))


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


async def test_get_includes_lifecycle_and_host_status(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    profile = SshConnectionProfile(
        id="profile-1",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
    )
    state = SimpleNamespace(
        host_id="e932ccae9eeb8f2a86f7ebfc5089c28d",
        desired_state="connected",
        phase="ready",
        last_error=None,
        attempt=2,
        next_attempt_at=None,
        updated_at=1_786_000_000,
    )
    app.state.ssh_host_manager = SimpleNamespace(snapshot=lambda: {"profile-1": state})
    app.state.host_store = SimpleNamespace(is_online=lambda _host_id: True)
    with patch(
        "omnigent.server.routes.ssh_connections.read_ssh_connections",
        return_value=[profile],
    ):
        with patch(
            "omnigent.server.routes.ssh_connections.read_ssh_settings",
            return_value=SshSettings(package_index_url="https://pypi.example.com/simple"),
        ):
            response = await client.get("/v1/ssh/connections")
    assert response.status_code == 200
    body = response.json()
    connection = body["connections"][0]
    assert connection["host_id"] == state.host_id
    assert connection["phase"] == "ready"
    assert connection["status"] == "online"
    assert connection["attempt"] == 2
    assert body["package_index_url"] == "https://pypi.example.com/simple"


async def test_retry_action_queues_immediate_attempt(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    retried: list[str] = []
    app.state.ssh_host_manager = SimpleNamespace(
        snapshot=lambda: {"profile-1": SimpleNamespace(desired_state="connected")},
        retry=lambda connection_id: retried.append(connection_id) is None,
    )
    response = await client.post("/v1/ssh/connections/profile-1/retry")
    assert response.status_code == 200
    assert response.json() == {"queued": True}
    assert retried == ["profile-1"]


async def test_retry_unknown_connection_is_not_found(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    """A detaching connection reports a conflict, an absent one a 404."""
    app.state.ssh_host_manager = SimpleNamespace(
        snapshot=lambda: {"profile-1": SimpleNamespace(desired_state="detached")},
        retry=lambda _connection_id: False,
    )
    missing = await client.post("/v1/ssh/connections/profile-9/retry")
    assert missing.status_code == 404

    detaching = await client.post("/v1/ssh/connections/profile-1/retry")
    assert detaching.status_code == 409


async def test_logs_endpoint_returns_captured_entries(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    """The settings UI reads installation lifecycle events for visibility."""
    from omnigent.server.ssh_host_manager import SshHostLogEntry

    entry = SshHostLogEntry(timestamp=1_786_000_000, phase="ready", level="info", message="ok")
    app.state.ssh_host_manager = SimpleNamespace(
        snapshot=lambda: {"profile-1": SimpleNamespace(desired_state="connected")},
        logs=lambda _connection_id: [entry],
    )
    response = await client.get("/v1/ssh/connections/profile-1/logs")
    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["entries"][0]["phase"] == "ready"
    assert body["entries"][0]["message"] == "ok"


async def test_logs_unknown_connection_is_not_found(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    app.state.ssh_host_manager = SimpleNamespace(
        snapshot=lambda: {},
        logs=lambda _connection_id: [],
    )
    response = await client.get("/v1/ssh/connections/profile-9/logs")
    assert response.status_code == 404


async def test_put_rejects_unsafe_id_and_duplicate_alias(client: httpx.AsyncClient) -> None:
    unsafe = await client.put(
        "/v1/ssh/connections",
        json={
            "connections": [
                {
                    "id": 'bad$(touch "$HOME/pwned")',
                    "label": "Bad",
                    "alias": "arca.ssh",
                }
            ]
        },
    )
    assert unsafe.status_code == 400

    duplicate = await client.put(
        "/v1/ssh/connections",
        json={
            "connections": [
                {"id": "one", "label": "One", "alias": "arca.ssh"},
                {"id": "two", "label": "Two", "alias": "arca.ssh"},
            ]
        },
    )
    assert duplicate.status_code == 400


async def test_non_admins_can_read_but_not_provision_ssh_targets() -> None:
    """Provisioning runs commands on remote machines, so it is admin-only.

    Reading the roster stays open so a non-admin's settings page still renders.
    """
    auth_provider = SimpleNamespace(get_user_id=lambda _request: "member@example.com")
    permission_store = SimpleNamespace(is_admin=lambda _user_id: False)
    app = FastAPI()
    app.include_router(
        create_ssh_connections_router(
            auth_provider=auth_provider,
            permission_store=permission_store,
        ),
        prefix="/v1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as local_client:
        with patch(
            "omnigent.server.routes.ssh_connections.read_ssh_connections",
            return_value=[],
        ):
            readable = await local_client.get("/v1/ssh/connections")
        written = await local_client.put(
            "/v1/ssh/connections",
            json={"connections": [{"label": "Arca", "alias": "arca.ssh"}]},
        )
        retried = await local_client.post("/v1/ssh/connections/profile-1/retry")
        tested = await local_client.post("/v1/ssh/test", json={"alias": "arca.ssh"})

    assert readable.status_code == 200
    assert written.status_code == 403
    assert retried.status_code == 403
    assert tested.status_code == 403
