"""Tests for ``/v1/skills`` routes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes.skills import create_skills_router
from omnigent.stores.host_store import HostStore

LOCAL_HOST_ID = "a" * 32
REMOTE_HOST_ID = "b" * 32
ALL_SKILL_HARNESSES = {"claude": True, "codex": True, "cursor": True}


def _configure_skills(host_store: HostStore, host_id: str) -> None:
    host_store.update_skill_configuration(
        host_id,
        ALL_SKILL_HARNESSES,
        [
            {"harness": harness, "rel_home_path": f".{harness}/skills"}
            for harness in ("claude", "codex", "cursor")
        ],
    )


@pytest.fixture
def skills_app(tmp_path):
    host_store = HostStore(f"sqlite:///{tmp_path / 'skills.db'}")
    host_store.upsert_on_connect(
        LOCAL_HOST_ID,
        "Local",
        "local",
        configured_harnesses=ALL_SKILL_HARNESSES,
    )
    _configure_skills(host_store, LOCAL_HOST_ID)
    registry = HostRegistry()
    registry.record_skill_inventory(
        LOCAL_HOST_ID,
        [
            {
                "name": "demo",
                "description": "demo",
                "harness": "claude",
                "rel_home_path": ".claude/skills/demo",
                "content_sha256": "abc",
            },
            {
                "name": "demo",
                "description": "demo",
                "harness": "codex",
                "rel_home_path": ".codex/skills/demo",
                "content_sha256": "abc",
            },
            {
                "name": "demo",
                "description": "demo",
                "harness": "cursor",
                "rel_home_path": ".cursor/skills/demo",
                "content_sha256": "abc",
            },
        ],
    )
    return registry, host_store


@pytest.fixture
async def client(skills_app) -> AsyncIterator[httpx.AsyncClient]:
    registry, host_store = skills_app
    app = FastAPI()
    app.include_router(
        create_skills_router(
            registry,
            host_store=host_store,
            auth_provider=None,
        ),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_list_skills_returns_manifest(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/skills")
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert payload["data"][0]["name"] == "demo"
    assert payload["data"][0]["synced"] is True
    variant = payload["data"][0]["variants"][0]
    assert variant["active_count"] == 3
    assert payload["data"][0]["sync_status"] == "synced"
    assert variant["occurrences"][0]["harness"] == "claude"
    assert variant["occurrences"][0]["rel_home_path"] == ".claude/skills/demo"


@pytest.mark.asyncio
async def test_refresh_skills_requests_fresh_inventory_from_connected_host(
    skills_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, host_store = skills_app
    monkeypatch.setattr(registry, "get", lambda _host_id: object())

    async def fresh_inventory(**_kwargs):
        return {
            "inventory": [
                {
                    "name": "new-skill",
                    "description": "New",
                    "harness": "claude",
                    "rel_home_path": ".claude/skills/new-skill",
                    "content_sha256": "fresh",
                }
            ]
        }

    monkeypatch.setattr(
        "omnigent.server.routes.skills.read_workspace_from_host",
        fresh_inventory,
    )
    app = FastAPI()
    app.include_router(
        create_skills_router(registry, host_store=host_store, auth_provider=None),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.post("/v1/skills/refresh")

    assert response.status_code == 200
    assert response.json()["refreshed"] == 1
    inventory = registry.skill_inventory(LOCAL_HOST_ID)
    assert inventory is not None
    assert [entry["name"] for entry in inventory] == ["new-skill"]


@pytest.mark.asyncio
async def test_existing_omnigent_copy_is_an_optional_variant(skills_app) -> None:
    registry, host_store = skills_app
    inventory = registry.skill_inventory(LOCAL_HOST_ID)
    assert inventory is not None
    registry.record_skill_inventory(
        LOCAL_HOST_ID,
        [
            *inventory,
            {
                "name": "demo",
                "description": "demo",
                "harness": "omnigent",
                "rel_home_path": ".omnigent/skills/demo",
                "content_sha256": "different",
            },
        ],
    )
    app = FastAPI()
    app.include_router(
        create_skills_router(registry, host_store=host_store, auth_provider=None),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/v1/skills")

    skill = response.json()["data"][0]
    assert skill["sync_status"] == "not_synced"
    omnigent = next(
        harness
        for harness in skill["hosts"][0]["harnesses"]
        if harness["harness"] == "omnigent"
    )
    assert omnigent["state"] == "present"
    assert omnigent["occurrence"]["rel_home_path"] == ".omnigent/skills/demo"


@pytest.mark.asyncio
async def test_offline_or_missing_report_is_not_synced(skills_app) -> None:
    registry, host_store = skills_app
    host_store.upsert_on_connect(
        REMOTE_HOST_ID,
        "Remote",
        "local",
        configured_harnesses=ALL_SKILL_HARNESSES,
    )
    _configure_skills(host_store, REMOTE_HOST_ID)
    app = FastAPI()
    app.include_router(
        create_skills_router(registry, host_store, auth_provider=None),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/v1/skills")
    assert response.json()["data"][0]["synced"] is False


@pytest.mark.asyncio
async def test_uninstalled_harnesses_are_partially_synced(skills_app) -> None:
    registry, host_store = skills_app
    host_store.update_harness_readiness(
        LOCAL_HOST_ID,
        {"claude": True, "codex": False, "cursor": False},
    )
    registry.record_skill_inventory(
        LOCAL_HOST_ID,
        [
            {
                "name": "demo",
                "description": "demo",
                "harness": "claude",
                "rel_home_path": ".claude/skills/demo",
                "content_sha256": "abc",
            }
        ],
    )
    app = FastAPI()
    app.include_router(
        create_skills_router(registry, host_store, auth_provider=None),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/v1/skills")

    skill = response.json()["data"][0]
    assert skill["sync_status"] == "partial"
    assert [cell["state"] for cell in skill["hosts"][0]["harnesses"]] == [
        "present",
        "unavailable",
        "unavailable",
    ]


@pytest.mark.asyncio
async def test_disabled_harness_is_ignored_for_sync_status(skills_app) -> None:
    registry, host_store = skills_app
    registry.record_skill_inventory(
        LOCAL_HOST_ID,
        [
            {
                "name": "demo",
                "description": "demo",
                "harness": harness,
                "rel_home_path": f".{harness}/skills/demo",
                "content_sha256": "abc",
            }
            for harness in ("claude", "codex")
        ],
    )
    host_store.update_skill_sync_harnesses(
        LOCAL_HOST_ID,
        {"claude": True, "codex": True, "cursor": False},
    )
    app = FastAPI()
    app.include_router(
        create_skills_router(registry, host_store, auth_provider=None),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/v1/skills")

    skill = response.json()["data"][0]
    assert skill["sync_status"] == "synced"
    assert {row["harness"] for row in skill["hosts"][0]["harnesses"]} == {
        "claude",
        "codex",
    }


@pytest.mark.asyncio
async def test_disabled_harness_only_shows_a_distinct_existing_variant(skills_app) -> None:
    registry, host_store = skills_app
    inventory = registry.skill_inventory(LOCAL_HOST_ID)
    assert inventory is not None
    registry.record_skill_inventory(
        LOCAL_HOST_ID,
        [
            *[entry for entry in inventory if entry["harness"] != "cursor"],
            {
                "name": "demo",
                "description": "demo",
                "harness": "cursor",
                "rel_home_path": ".cursor/skills/demo",
                "content_sha256": "different",
            },
        ],
    )
    host_store.update_skill_sync_harnesses(
        LOCAL_HOST_ID,
        {"claude": True, "codex": True, "cursor": False},
    )
    app = FastAPI()
    app.include_router(
        create_skills_router(registry, host_store, auth_provider=None),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/v1/skills")

    skill = response.json()["data"][0]
    assert skill["sync_status"] == "synced"
    cursor = next(
        row
        for row in skill["hosts"][0]["harnesses"]
        if row["harness"] == "cursor"
    )
    assert cursor["state"] == "ignored_variant"
    assert cursor["occurrence"]["content_sha256"] == "different"


@pytest.mark.asyncio
async def test_disabled_harness_shows_the_only_existing_variant(skills_app) -> None:
    registry, host_store = skills_app
    registry.record_skill_inventory(
        LOCAL_HOST_ID,
        [
            {
                "name": "demo",
                "description": "demo",
                "harness": "cursor",
                "rel_home_path": ".cursor/skills/demo",
                "content_sha256": "cursor-only",
            }
        ],
    )
    host_store.update_skill_sync_harnesses(
        LOCAL_HOST_ID,
        {"claude": True, "codex": True, "cursor": False},
    )
    app = FastAPI()
    app.include_router(
        create_skills_router(registry, host_store, auth_provider=None),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/v1/skills")

    skill = response.json()["data"][0]
    assert skill["sync_status"] == "not_synced"
    cursor = next(
        row
        for row in skill["hosts"][0]["harnesses"]
        if row["harness"] == "cursor"
    )
    assert cursor["state"] == "ignored_variant"
    assert cursor["occurrence"]["content_sha256"] == "cursor-only"


@pytest.mark.asyncio
async def test_groups_occurrences_into_variants_by_hash(skills_app) -> None:
    registry, host_store = skills_app
    host_store.upsert_on_connect(
        REMOTE_HOST_ID,
        "Remote",
        "local",
        configured_harnesses=ALL_SKILL_HARNESSES,
    )
    _configure_skills(host_store, REMOTE_HOST_ID)
    registry.record_skill_inventory(
        LOCAL_HOST_ID,
        [
            {
                "name": "demo",
                "description": "demo",
                "harness": "claude",
                "rel_home_path": ".claude/skills/demo",
                "content_sha256": "abc",
            },
            {
                "name": "demo",
                "description": "demo",
                "harness": "codex",
                "rel_home_path": ".codex/skills/demo",
                "content_sha256": "different",
            },
        ],
    )
    registry.record_skill_inventory(
        REMOTE_HOST_ID,
        [
            {
                "name": "demo",
                "description": "demo",
                "harness": "cursor",
                "rel_home_path": ".cursor/skills/demo",
                "content_sha256": "abc",
            }
        ],
    )
    app = FastAPI()
    app.include_router(
        create_skills_router(registry, host_store, auth_provider=None),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/v1/skills")

    skill = response.json()["data"][0]
    assert skill["synced"] is False
    assert [variant["content_sha256"] for variant in skill["variants"]] == [
        "abc",
        "different",
    ]
    assert skill["variants"][0]["active_count"] == 2
    assert {
        occurrence["harness"] for occurrence in skill["variants"][0]["occurrences"]
    } == {"claude", "cursor"}
