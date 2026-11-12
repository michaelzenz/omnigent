"""Tests for the caller-scoped settings routes (``/v1/settings``) and role definitions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import UnifiedAuthProvider
from omnigent.server.routes import user_settings
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

ROUTE = "/v1/settings/session-title-instructions"
GLOBALHUB_ROUTE = "/v1/settings/globalhub"
GLOBALHUB_UNSET = {"host_id": None, "workspace": None}
ROLE_PROFILES_ROUTE = "/v1/agent-tasks/roles/profiles"
MANAGER_PROFILE_ROUTE = "/v1/agent-tasks/roles/manager:default/profile"
ALICE = "alice@example.com"
BOB = "bob@example.com"


def _as_user(user: str) -> dict[str, str]:
    return {"X-Forwarded-Email": user}


@pytest.fixture()
def multi_user_app(runtime_init: None, db_uri: str, tmp_path: Path) -> FastAPI:
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        auth_provider=UnifiedAuthProvider(source="header", local_single_user=False),
        permission_store=SqlAlchemyPermissionStore(db_uri),
        host_store=HostStore(db_uri),
    )


@pytest_asyncio.fixture()
async def multi_user_client(multi_user_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=multi_user_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_single_user_round_trip(client: httpx.AsyncClient) -> None:
    initial = await client.get(ROUTE)
    assert initial.status_code == 200
    assert initial.json() == {"instructions": None, "max_chars": 4000}

    saved = await client.put(ROUTE, json={"instructions": "  Start with an emoji.\n"})
    assert saved.status_code == 200
    assert saved.json() == {"instructions": "Start with an emoji.", "max_chars": 4000}
    assert (await client.get(ROUTE)).json()["instructions"] == "Start with an emoji."

    cleared = await client.put(ROUTE, json={"instructions": "   "})
    assert cleared.json()["instructions"] is None
    assert (await client.get(ROUTE)).json()["instructions"] is None


async def test_rejects_instructions_over_limit(client: httpx.AsyncClient) -> None:
    await client.put(ROUTE, json={"instructions": "Keep me."})

    response = await client.put(ROUTE, json={"instructions": "x" * 4001})

    assert response.status_code == 400
    assert "4000 characters" in response.text
    assert (await client.get(ROUTE)).json()["instructions"] == "Keep me."


async def test_accepts_limit_after_stripping(client: httpx.AsyncClient) -> None:
    response = await client.put(ROUTE, json={"instructions": "  " + "x" * 4000 + "  "})

    assert response.status_code == 200
    assert response.json()["instructions"] == "x" * 4000


@pytest.mark.parametrize(
    ("route", "body"),
    [
        (ROUTE, {}),
        (ROUTE, {"instructions": 7}),
        (ROUTE, {"instructions": "ok", "extra": True}),
        (GLOBALHUB_ROUTE, {"host_id": "host_alice"}),
        (GLOBALHUB_ROUTE, {"host_id": "host_alice", "workspace": None}),
        (GLOBALHUB_ROUTE, {"host_id": None, "workspace": "/Users/alice"}),
        (GLOBALHUB_ROUTE, {"host_id": "", "workspace": "/Users/alice"}),
        (GLOBALHUB_ROUTE, {"host_id": "host_alice", "workspace": "/Users/alice", "extra": True}),
        (MANAGER_PROFILE_ROUTE, {"manual": 7}),
        (MANAGER_PROFILE_ROUTE, {"manual": "x" * 64_001}),
        (MANAGER_PROFILE_ROUTE, {"title": "Renamed"}),
    ],
)
async def test_rejects_malformed_body(
    client: httpx.AsyncClient, route: str, body: dict[str, object]
) -> None:
    assert (await client.put(route, json=body)).status_code == 422


async def test_globalhub_round_trip_stores_canonical_workspace(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    validations: list[dict[str, object]] = []

    async def canonical_workspace(**kwargs: object) -> str:
        validations.append(kwargs)
        return "/Users/alice/globalhub"

    monkeypatch.setattr(
        user_settings, "validate_uploaded_bundle_host_workspace", canonical_workspace
    )
    assert (await client.get(GLOBALHUB_ROUTE)).json() == GLOBALHUB_UNSET

    saved = await client.put(
        GLOBALHUB_ROUTE, json={"host_id": "host_alice", "workspace": "/Users/alice/./globalhub"}
    )

    placement = {"host_id": "host_alice", "workspace": "/Users/alice/globalhub"}
    assert saved.json() == placement
    assert (await client.get(GLOBALHUB_ROUTE)).json() == placement
    assert [
        (validation["host_id"], validation["workspace"], validation["spec_cwd"])
        for validation in validations
    ] == [("host_alice", "/Users/alice/./globalhub", None)]

    cleared = await client.put(GLOBALHUB_ROUTE, json=GLOBALHUB_UNSET)
    assert cleared.json() == GLOBALHUB_UNSET
    assert (await client.get(GLOBALHUB_ROUTE)).json() == GLOBALHUB_UNSET
    assert len(validations) == 1


async def test_globalhub_rejects_another_users_host(
    multi_user_app: FastAPI, multi_user_client: httpx.AsyncClient
) -> None:
    bob_host_id = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    multi_user_app.state.host_store.upsert_on_connect(bob_host_id, "bob-laptop", BOB)

    response = await multi_user_client.put(
        GLOBALHUB_ROUTE,
        json={"host_id": bob_host_id, "workspace": "/Users/bob"},
        headers=_as_user(ALICE),
    )

    assert response.status_code == 403
    alice = await multi_user_client.get(GLOBALHUB_ROUTE, headers=_as_user(ALICE))
    assert alice.json() == GLOBALHUB_UNSET


async def test_role_profiles_list_defaults_without_manuals(client: httpx.AsyncClient) -> None:
    listed = (await client.get(ROLE_PROFILES_ROUTE)).json()["data"]
    managers = (await client.get(ROLE_PROFILES_ROUTE, params={"kind": "manager"})).json()["data"]

    assert [profile["role"] for profile in listed] == ["broker", "manager:default", "custodian"]
    assert all(profile["customized"] == [] and profile["model"] is None for profile in listed)
    assert all("manual" not in profile for profile in listed)
    assert [profile["role"] for profile in managers] == ["manager:default"]


async def test_role_profile_override_and_reset(client: httpx.AsyncClient) -> None:
    default = (await client.get(MANAGER_PROFILE_ROUTE)).json()
    assert default["manual"].startswith("# Task manager manual")

    saved = await client.put(
        MANAGER_PROFILE_ROUTE, json={"manual": "  Route nothing.\n", "model": "example-model"}
    )

    assert saved.status_code == 200, saved.text
    assert (saved.json()["manual"], saved.json()["model"], saved.json()["customized"]) == (
        "Route nothing.",
        "example-model",
        ["model", "manual"],
    )

    reset = await client.put(MANAGER_PROFILE_ROUTE, json={"manual": None})

    assert reset.json()["manual"] == default["manual"]
    assert (reset.json()["model"], reset.json()["customized"]) == ("example-model", ["model"])
    assert (await client.get(MANAGER_PROFILE_ROUTE)).json() == reset.json()


async def test_unknown_role_profile_is_not_found(client: httpx.AsyncClient) -> None:
    route = "/v1/agent-tasks/roles/secretary/profile"

    assert (await client.get(route)).status_code == 404
    assert (await client.put(route, json={"manual": "x"})).status_code == 404


async def test_role_profiles_are_private_to_each_user(
    multi_user_client: httpx.AsyncClient,
) -> None:
    await multi_user_client.put(
        MANAGER_PROFILE_ROUTE, json={"manual": "Alice manual."}, headers=_as_user(ALICE)
    )

    alice = await multi_user_client.get(MANAGER_PROFILE_ROUTE, headers=_as_user(ALICE))
    bob = await multi_user_client.get(MANAGER_PROFILE_ROUTE, headers=_as_user(BOB))

    assert alice.json()["manual"] == "Alice manual."
    assert bob.json()["customized"] == []


async def test_info_advertises_capability(client: httpx.AsyncClient) -> None:
    info = await client.get("/v1/info")

    assert info.json()["session_title_instructions"] is True


async def test_settings_are_private_to_each_user(multi_user_client: httpx.AsyncClient) -> None:
    await multi_user_client.put(
        ROUTE, json={"instructions": "Alice rule."}, headers=_as_user(ALICE)
    )

    alice = await multi_user_client.get(ROUTE, headers=_as_user(ALICE))
    bob = await multi_user_client.get(ROUTE, headers=_as_user(BOB))

    assert alice.json()["instructions"] == "Alice rule."
    assert bob.json()["instructions"] is None


async def test_requires_authenticated_user(multi_user_client: httpx.AsyncClient) -> None:
    assert (await multi_user_client.get(ROUTE)).status_code == 401
    assert (await multi_user_client.put(ROUTE, json={"instructions": "x"})).status_code == 401
    assert (await multi_user_client.get(GLOBALHUB_ROUTE)).status_code == 401
    unset = await multi_user_client.put(GLOBALHUB_ROUTE, json=GLOBALHUB_UNSET)
    assert unset.status_code == 401
    assert (await multi_user_client.get(ROLE_PROFILES_ROUTE)).status_code == 401
    manual = await multi_user_client.put(MANAGER_PROFILE_ROUTE, json={"manual": "x"})
    assert manual.status_code == 401
