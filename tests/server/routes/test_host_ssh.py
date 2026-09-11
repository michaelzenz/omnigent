"""Tests for the host-scoped SSH attach API (desktop app <-> server)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from omnigent.db.db_models import OmnigentBase
from omnigent.db.utils import get_or_create_engine
from omnigent.entities import SshConnectionProfile
from omnigent.server.routes.host_ssh import create_host_ssh_router
from omnigent.server.ssh_logs import SshLogRing
from omnigent.stores.ssh_host_installation_store import SshHostInstallationStore
from omnigent.version import VERSION

_HOST_HEADER = {"X-Omnigent-Host-Id": "daemon-1"}


def _build_app(
    tmp_path,
    *,
    owner: str | None = "alice@example.com",
    host_online: bool = True,
    sandbox_provider: str | None = None,
):
    uri = f"sqlite:///{tmp_path / 'host-ssh.db'}"
    OmnigentBase.metadata.create_all(get_or_create_engine(uri))
    store = SshHostInstallationStore(uri)
    conn = SimpleNamespace(owner=owner)
    registry = SimpleNamespace(
        get=lambda host_id: conn if host_id in ("daemon-1", "daemon-2") else None,
    )
    host_store = SimpleNamespace(
        get_host=lambda _host_id: SimpleNamespace(sandbox_provider=sandbox_provider),
        register_ssh_host=lambda **kwargs: SimpleNamespace(**kwargs),
        delete_host=lambda _host_id: None,
    )
    # The router reads host_is_live from the module at call time.
    import omnigent.stores.host_store as host_store_module

    host_store_module.host_is_live = lambda _host: host_online  # type: ignore[assignment]

    app = FastAPI()
    app.state.ssh_logs = SshLogRing()
    app.include_router(
        create_host_ssh_router(
            ssh_store=store,
            host_store=host_store,  # type: ignore[arg-type]
            host_registry=registry,  # type: ignore[arg-type]
            ssh_logs=app.state.ssh_logs,
        ),
        prefix="/v1",
    )
    return app, store


async def test_assignments_are_scoped_to_the_daemons_owner(tmp_path) -> None:
    app, store = _build_app(tmp_path)
    mine = SshConnectionProfile(
        id="mine", label="Mine", alias="mine-box", created_at="2026-01-01T00:00:00+00:00"
    )
    theirs = SshConnectionProfile(
        id="theirs", label="Theirs", alias="their-box", created_at="2026-01-01T00:00:00+00:00"
    )
    store.sync_connections({mine.id: mine}, bundle_version="test", owner="alice@example.com")
    store.sync_connections({theirs.id: theirs}, bundle_version="test", owner="bob@example.com")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/host/ssh/assignments", headers=_HOST_HEADER)

    assert resp.status_code == 200
    body = resp.json()
    assert [row["connection_id"] for row in body["connections"]] == ["mine"]
    assert body["server_version"] == VERSION
    assert body["settings"]["remote_namespace"]


async def test_assignments_require_connected_host(tmp_path) -> None:
    app, _store = _build_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing_header = await client.get("/v1/host/ssh/assignments")
        unknown_host = await client.get(
            "/v1/host/ssh/assignments", headers={"X-Omnigent-Host-Id": "daemon-9"}
        )
    assert missing_header.status_code == 401
    assert unknown_host.status_code == 401


async def test_phase_push_is_generation_checked_and_owner_scoped(tmp_path) -> None:
    app, store = _build_app(tmp_path)
    profile = SshConnectionProfile(
        id="conn-1", label="Box", alias="box", created_at="2026-01-01T00:00:00+00:00"
    )
    store.sync_connections({profile.id: profile}, bundle_version="test", owner="alice@example.com")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        claimed = await client.post(
            "/v1/host/ssh/connections/conn-1/claim",
            json={"lease_seconds": 30},
            headers=_HOST_HEADER,
        )
        assert claimed.status_code == 200
        assert claimed.json()["claimed"] is True

        ok = await client.post(
            "/v1/host/ssh/connections/conn-1/phase",
            json={"generation": 0, "phase": "waiting_for_ssh"},
            headers=_HOST_HEADER,
        )
        assert ok.status_code == 200
        assert ok.json()["accepted"] is True

        stale = await client.post(
            "/v1/host/ssh/connections/conn-1/phase",
            json={"generation": 1, "phase": "ready"},
            headers=_HOST_HEADER,
        )
        assert stale.json()["superseded"] is True

        other_user = await client.post(
            "/v1/host/ssh/connections/conn-1/phase",
            json={"generation": 1, "phase": "ready"},
            headers={"X-Omnigent-Host-Id": "daemon-9"},
        )
        assert other_user.status_code == 401


async def test_claim_blocks_a_second_daemon(tmp_path) -> None:
    """Two desktop instances of one user: only one executor per connection."""
    app, store = _build_app(tmp_path, owner="alice@example.com")
    profile = SshConnectionProfile(
        id="conn-1", label="Box", alias="box", created_at="2026-01-01T00:00:00+00:00"
    )
    store.sync_connections({profile.id: profile}, bundle_version="test", owner="alice@example.com")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/host/ssh/connections/conn-1/claim",
            json={"lease_seconds": 300},
            headers=_HOST_HEADER,
        )
        second = await client.post(
            "/v1/host/ssh/connections/conn-1/claim",
            json={"lease_seconds": 30},
            headers={"X-Omnigent-Host-Id": "daemon-2"},
        )
    assert first.json()["claimed"] is True
    assert second.json()["claimed"] is False


async def test_logs_push_feeds_the_settings_ring(tmp_path) -> None:
    app, store = _build_app(tmp_path)
    profile = SshConnectionProfile(
        id="conn-1", label="Box", alias="box", created_at="2026-01-01T00:00:00+00:00"
    )
    store.sync_connections({profile.id: profile}, bundle_version="test", owner="alice@example.com")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/host/ssh/connections/conn-1/logs",
            json={"entries": [{"phase": "installing", "level": "info", "message": "hi"}]},
            headers=_HOST_HEADER,
        )
    assert resp.status_code == 200
    ring: SshLogRing = app.state.ssh_logs
    entries = ring.entries("conn-1")
    assert len(entries) == 1
    assert entries[0].message == "hi"


async def test_register_remote_host_returns_token_for_owner_row(tmp_path) -> None:
    app, store = _build_app(tmp_path)
    profile = SshConnectionProfile(
        id="conn-1", label="Build box", alias="box", created_at="2026-01-01T00:00:00+00:00"
    )
    store.sync_connections({profile.id: profile}, bundle_version="test", owner="alice@example.com")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/host/ssh/connections/conn-1/register-remote-host",
            json={},
            headers=_HOST_HEADER,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["host_id"] == store.snapshots("alice@example.com")["conn-1"].host_id
    assert body["name"].startswith("Build box")
    assert body["token"]


async def test_version_skew_requeues_rows(tmp_path) -> None:
    """Rows pinned to an older server version get one requeue on pull."""
    app, store = _build_app(tmp_path)
    profile = SshConnectionProfile(
        id="conn-1", label="Box", alias="box", created_at="2026-01-01T00:00:00+00:00"
    )
    store.sync_connections(
        {profile.id: profile}, bundle_version="0.0.1-old", owner="alice@example.com"
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/v1/host/ssh/assignments", headers=_HOST_HEADER)

    row = store.snapshots("alice@example.com")["conn-1"]
    assert row.bundle_version == VERSION  # install target now the server's version
    assert row.phase == "queued"
    assert row.generation == 1


async def test_managed_sandbox_hosts_are_rejected(tmp_path) -> None:
    """Server-provisioned sandboxes share the user's identity but are not the
    user's machine; SSH attach must never execute from them."""
    app, store = _build_app(tmp_path, sandbox_provider="modal")
    profile = SshConnectionProfile(
        id="conn-1", label="Box", alias="box", created_at="2026-01-01T00:00:00+00:00"
    )
    store.sync_connections({profile.id: profile}, bundle_version="test", owner="alice@example.com")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assignments = await client.get("/v1/host/ssh/assignments", headers=_HOST_HEADER)
        claimed = await client.post(
            "/v1/host/ssh/connections/conn-1/claim",
            json={"lease_seconds": 30},
            headers=_HOST_HEADER,
        )
    assert assignments.status_code == 403
    assert claimed.status_code == 403
    assert "managed sandbox" in assignments.json()["detail"]


async def test_phase_reset_attempt_clears_counter(tmp_path) -> None:
    app, store = _build_app(tmp_path)
    profile = SshConnectionProfile(
        id="conn-1", label="Box", alias="box", created_at="2026-01-01T00:00:00+00:00"
    )
    store.sync_connections({profile.id: profile}, bundle_version="test", owner="alice@example.com")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        claim = await client.post(
            "/v1/host/ssh/connections/conn-1/claim",
            json={"lease_seconds": 30},
            headers=_HOST_HEADER,
        )
        assert claim.json()["claimed"] is True
        for _ in range(3):
            await client.post(
                "/v1/host/ssh/connections/conn-1/phase",
                json={"generation": 0, "phase": "backoff", "increment_attempt": True},
                headers=_HOST_HEADER,
            )
        assert store.snapshots("alice@example.com")["conn-1"].attempt == 3

        ok = await client.post(
            "/v1/host/ssh/connections/conn-1/phase",
            json={"generation": 0, "phase": "ready", "reset_attempt": True},
            headers=_HOST_HEADER,
        )
        assert ok.json()["accepted"] is True
        assert store.snapshots("alice@example.com")["conn-1"].attempt == 0


async def test_claim_returns_current_phase_for_refresh(tmp_path) -> None:
    """retry_now flips phase to queued; the claim must surface it so the
    executor's ready short-circuit is bypassed (refresh = real restart)."""
    app, store = _build_app(tmp_path)
    profile = SshConnectionProfile(
        id="conn-1", label="Box", alias="box", created_at="2026-01-01T00:00:00+00:00"
    )
    store.sync_connections({profile.id: profile}, bundle_version="test", owner="alice@example.com")
    # Row settled at ready.
    leased = store.acquire("conn-1", lease_owner="daemon-1", lease_seconds=300)
    assert leased is not None
    assert store.set_phase(
        "conn-1", lease_owner="daemon-1", generation=leased.generation, phase="ready"
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Refresh: retry_now (the user route's backing store call) queues the
        # row and bumps its generation.
        assert store.retry_now("conn-1", owner="alice@example.com") is True

        claimed = await client.post(
            "/v1/host/ssh/connections/conn-1/claim",
            json={"lease_seconds": 30},
            headers=_HOST_HEADER,
        )
    body = claimed.json()
    assert body["claimed"] is True
    assert body["phase"] == "queued"
    assert body["generation"] == 1
