"""Tests for the script plugin health routes."""

from __future__ import annotations

import httpx
import pytest

from omnigent.host.identity import HOST_ID_HEADER
from omnigent.server.routes.script_plugin_health import get_plugin_health_store


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    store = get_plugin_health_store()
    with store._lock:
        store._records.clear()


async def test_post_then_get_round_trip(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/agent-tasks/script-plugins/health",
        headers={HOST_ID_HEADER: "host-A"},
        json={
            "plugins": [
                {
                    "name": "slack_watch",
                    "kind": "poll",
                    "outcome": "ok",
                    "last_run_at": 1000.0,
                    "consecutive_failures": 0,
                }
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "count": 1}

    got = await client.get("/v1/agent-tasks/script-plugins/health?kind=poll")
    assert got.status_code == 200
    plugins = got.json()["plugins"]
    assert len(plugins) == 1
    row = plugins[0]
    assert row["host_id"] == "host-A"
    assert row["name"] == "slack_watch"
    assert row["kind"] == "poll"
    assert row["outcome"] == "ok"
    assert row["warning"] is None


async def test_warning_round_trips(client: httpx.AsyncClient) -> None:
    await client.post(
        "/v1/agent-tasks/script-plugins/health",
        headers={HOST_ID_HEADER: "host-A"},
        json={
            "plugins": [
                {
                    "name": "dup",
                    "kind": "poll",
                    "outcome": "ok",
                    "warning": "duplicate plugin name exists in both roots",
                }
            ]
        },
    )
    rows = (await client.get("/v1/agent-tasks/script-plugins/health?kind=poll")).json()["plugins"]
    assert rows[0]["warning"] == "duplicate plugin name exists in both roots"


async def test_kind_filter_only_poll(client: httpx.AsyncClient) -> None:
    await client.post(
        "/v1/agent-tasks/script-plugins/health",
        headers={HOST_ID_HEADER: "host-A"},
        json={"plugins": [{"name": "p1", "kind": "poll", "outcome": "ok"}]},
    )
    polls = (await client.get("/v1/agent-tasks/script-plugins/health?kind=poll")).json()["plugins"]
    assert {p["name"] for p in polls} == {"p1"}


async def test_multiple_hosts_grouped_and_sorted(client: httpx.AsyncClient) -> None:
    for host in ("host-B", "host-A"):
        await client.post(
            "/v1/agent-tasks/script-plugins/health",
            headers={HOST_ID_HEADER: host},
            json={"plugins": [{"name": "x", "kind": "poll", "outcome": "ok"}]},
        )
    rows = (await client.get("/v1/agent-tasks/script-plugins/health")).json()["plugins"]
    host_ids = [r["host_id"] for r in rows]
    assert host_ids == ["host-A", "host-B"]


async def test_upsert_replaces_previous(client: httpx.AsyncClient) -> None:
    await client.post(
        "/v1/agent-tasks/script-plugins/health",
        headers={HOST_ID_HEADER: "host-A"},
        json={"plugins": [{"name": "x", "kind": "poll", "outcome": "ok"}]},
    )
    await client.post(
        "/v1/agent-tasks/script-plugins/health",
        headers={HOST_ID_HEADER: "host-A"},
        json={
            "plugins": [
                {"name": "x", "kind": "poll", "outcome": "exit_nonzero", "consecutive_failures": 3}
            ]
        },
    )
    rows = (await client.get("/v1/agent-tasks/script-plugins/health?kind=poll")).json()["plugins"]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "exit_nonzero"
    assert rows[0]["consecutive_failures"] == 3
