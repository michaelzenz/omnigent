"""Tests for SSH settings routes (per-user configuration, host-side execution)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from omnigent.db.utils import now_epoch
from omnigent.entities import SshConnectionProfile
from omnigent.server.routes.ssh_connections import create_ssh_connections_router
from omnigent.server.ssh_logs import SshLogRing
from omnigent.stores.host_store import Host


async def test_put_ssh_connections_persists_profiles(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    resp = await client.put(
        "/v1/ssh/connections",
        json={"connections": [{"label": "Arca", "alias": "arca.ssh"}]},
    )
    assert resp.status_code == 200
    ssh_store = app.state.ssh_host_installation_store
    profiles = ssh_store.profiles("local")
    assert len(profiles) == 1
    assert profiles[0].alias == "arca.ssh"
    assert profiles[0].label == "Arca"
    assert profiles[0].owner == "local"
    assert ssh_store.get_settings("local").package_index_url is None


async def test_put_ssh_connections_keeps_created_at_of_existing_profile(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    """Re-saving a profile must not reset when it was first added."""
    ssh_store = app.state.ssh_host_installation_store
    existing = SshConnectionProfile(
        id="profile-1",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
    )
    ssh_store.sync_connections(
        {existing.id: existing},
        bundle_version="test",
        owner="local",
    )
    resp = await client.put(
        "/v1/ssh/connections",
        json={"connections": [{"id": "profile-1", "label": "Arca II", "alias": "arca.ssh"}]},
    )
    assert resp.status_code == 200
    stored = ssh_store.profiles("local")[0]
    assert stored.created_at == "2026-01-01T00:00:00+00:00"
    assert stored.label == "Arca II"


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
    ssh_store = app.state.ssh_host_installation_store
    ssh_store.sync_connections({profile.id: profile}, bundle_version="test", owner="local")
    ssh_store.update_settings(
        owner="local",
        package_index_url="https://pypi.example.com/simple",
        npm_registry_url=None,
    )
    row = ssh_store.snapshots("local")["profile-1"]
    app.state.host_store = SimpleNamespace(
        get_host=lambda _host_id: Host(
            host_id=_host_id,
            name="laptop",
            user_id="local",
            status="online",
            created_at=1_786_000_000,
            updated_at=now_epoch(),
            consecutive_rapid_disconnects=0,
        ),
    )
    # Mark the row ready so the fake host maps to "online".
    leased = ssh_store.acquire("profile-1", lease_owner="host-1", lease_seconds=30)
    assert leased is not None
    assert ssh_store.set_phase(
        "profile-1", lease_owner="host-1", generation=leased.generation, phase="ready"
    )

    response = await client.get("/v1/ssh/connections")
    assert response.status_code == 200
    body = response.json()
    connection = body["connections"][0]
    assert connection["host_id"] == row.host_id
    assert connection["phase"] == "ready"
    assert connection["status"] == "online"
    assert body["package_index_url"] == "https://pypi.example.com/simple"
    assert body["npm_registry_url"] is None


async def test_get_includes_flaky_warning_for_rapid_disconnects(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    """The warning field appears when consecutive_rapid_disconnects >= 3."""
    profile = SshConnectionProfile(
        id="profile-1",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
    )
    ssh_store = app.state.ssh_host_installation_store
    ssh_store.sync_connections({profile.id: profile}, bundle_version="test", owner="local")
    app.state.host_store = SimpleNamespace(
        get_host=lambda _host_id: Host(
            host_id=_host_id,
            name="laptop",
            user_id="local",
            status="offline",
            created_at=1_786_000_000,
            updated_at=1_786_000_000,
            consecutive_rapid_disconnects=5,
        ),
    )
    response = await client.get("/v1/ssh/connections")
    assert response.status_code == 200
    connection = response.json()["connections"][0]
    assert "warning" in connection
    assert "flaky" in connection["warning"].lower()


async def test_retry_action_queues_immediate_attempt(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    ssh_store = app.state.ssh_host_installation_store
    profile = SshConnectionProfile(
        id="profile-1",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
    )
    ssh_store.sync_connections({profile.id: profile}, bundle_version="test", owner="local")
    response = await client.post("/v1/ssh/connections/profile-1/retry")
    assert response.status_code == 200
    assert response.json() == {"queued": True}
    row = ssh_store.snapshots("local")["profile-1"]
    assert row.phase == "queued"


async def test_retry_unknown_connection_is_not_found(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    """A detaching connection reports a conflict, an absent one a 404."""
    ssh_store = app.state.ssh_host_installation_store
    profile = SshConnectionProfile(
        id="profile-1",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
    )
    ssh_store.sync_connections({profile.id: profile}, bundle_version="test", owner="local")
    ssh_store.sync_connections({}, bundle_version="test", owner="local")

    missing = await client.post("/v1/ssh/connections/profile-9/retry")
    assert missing.status_code == 404

    detaching = await client.post("/v1/ssh/connections/profile-1/retry")
    assert detaching.status_code == 409


async def test_logs_endpoint_returns_captured_entries(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    """The settings UI reads installation lifecycle events for visibility."""
    ssh_store = app.state.ssh_host_installation_store
    profile = SshConnectionProfile(
        id="profile-1",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
    )
    ssh_store.sync_connections({profile.id: profile}, bundle_version="test", owner="local")
    ring: SshLogRing = app.state.ssh_logs
    ring.append("profile-1", phase="ready", level="info", message="ok")

    response = await client.get("/v1/ssh/connections/profile-1/logs")
    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["entries"][0]["phase"] == "ready"
    assert body["entries"][0]["message"] == "ok"


async def test_logs_unknown_connection_is_not_found(
    client: httpx.AsyncClient,
) -> None:
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


async def test_users_manage_only_their_own_connections() -> None:
    """Each user sees and syncs their own set; other users' rows are untouched."""
    calls: dict[str, list] = {"profiles": [], "synced": []}

    class _Store:
        def profiles(self, owner: str):
            calls["profiles"].append(owner)
            return []

        def snapshots(self, owner: str):
            return {}

        def get_settings(self, owner: str):
            from omnigent.entities import SshSettings

            return SshSettings(remote_namespace="test")

        def sync_connections(self, profiles, *, bundle_version, owner):
            calls["synced"].append(owner)

        def update_settings(self, **kwargs):
            return self.get_settings(kwargs["owner"])

        def requeue_connected_for_owner(self, owner: str) -> bool:
            return False

    auth = SimpleNamespace(get_user_id=lambda request: request.headers.get("x-test-user"))
    router_app = FastAPI()
    router_app.include_router(
        create_ssh_connections_router(
            ssh_store=_Store(),  # type: ignore[arg-type]
            auth_provider=auth,  # type: ignore[arg-type]
        ),
        prefix="/v1",
    )

    import httpx as _httpx

    async with _httpx.AsyncClient(
        transport=_httpx.ASGITransport(app=router_app),
        base_url="http://test",
    ) as local_client:
        for user in ("alice@example.com", "bob@example.com"):
            resp = await local_client.put(
                "/v1/ssh/connections",
                json={"connections": []},
                headers={"x-test-user": user},
            )
            assert resp.status_code == 200

    assert calls["synced"] == ["alice@example.com", "bob@example.com"]


async def test_ssh_test_forwards_to_owner_desktop_app(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    """The probe runs on the user's own host daemon, not the server."""
    sent: list[str] = []

    class _FakeConn:
        def __init__(self) -> None:
            self.owner = "local"
            self.pending_ssh_probes: dict = {}

        def send_text(self, frame: str) -> None:
            sent.append(frame)
            import json

            payload = json.loads(frame)
            future = self.pending_ssh_probes.pop(payload["request_id"])
            future.set_result({"ok": True, "message": "Connected", "latency_ms": 42})

    conn = _FakeConn()
    registry = SimpleNamespace(
        online_host_ids=lambda: ["host-1", "host-2"],
        get=lambda host_id: {"host-1": SimpleNamespace(owner="someone-else"), "host-2": conn}[
            host_id
        ],
        send_text=lambda c, frame: c.send_text(frame),
    )
    app.state.host_registry = registry

    resp = await client.post("/v1/ssh/test", json={"alias": "arca.ssh"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "message": "Connected", "latency_ms": 42}
    assert len(sent) == 1
    assert "arca.ssh" in sent[0]


async def test_ssh_test_without_desktop_app_is_unavailable(
    client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    """Browser-only setups have no executor; the probe reports that plainly."""
    registry = SimpleNamespace(
        online_host_ids=list,
        get=lambda _host_id: None,
        send_text=lambda _c, _frame: None,
    )
    app.state.host_registry = registry

    resp = await client.post("/v1/ssh/test", json={"alias": "arca.ssh"})
    assert resp.status_code == 503
    assert "Desktop app" in resp.json()["detail"]
