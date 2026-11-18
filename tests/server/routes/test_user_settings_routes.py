"""Tests for the caller-scoped settings routes (``/v1/settings``) and GlobalHub roles."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from omnigent.agent_tasks.session_labels import CUSTODIAN_ROLE_VALUE, ROLE_LABEL
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import UnifiedAuthProvider
from omnigent.server.routes import agent_task_roles, user_settings
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

ROUTE = "/v1/settings/session-title-instructions"
GLOBALHUB_ROUTE = "/v1/settings/globalhub"
GLOBALHUB_UNSET = {"host_id": None, "workspace": None}
GLOBALHUB_DEFAULTS = {**GLOBALHUB_UNSET, "report_new_sessions": True}
ROLE_PROFILES_ROUTE = "/v1/agent-tasks/roles/profiles"
MANAGER_PROFILE_ROUTE = "/v1/agent-tasks/roles/manager:default/profile"
CUSTODIAN_PROFILE_ROUTE = "/v1/agent-tasks/roles/custodian/profile"
CUSTODIAN_SESSION_ROUTE = "/v1/agent-tasks/roles/custodian/session"
WATCHERS_ROUTE = "/v1/agent-tasks/watchers"
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
        # The custodian's schedule moved to its role profile.
        (GLOBALHUB_ROUTE, {**GLOBALHUB_UNSET, "custodian_sweep": None}),
        (CUSTODIAN_PROFILE_ROUTE, {"schedule": {"enabled": True, "rrule": "FREQ=DAILY"}}),
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
    assert (await client.get(GLOBALHUB_ROUTE)).json() == GLOBALHUB_DEFAULTS

    saved = await client.put(
        GLOBALHUB_ROUTE,
        json={
            "host_id": "host_alice",
            "workspace": "/Users/alice/./globalhub",
            "report_new_sessions": False,
        },
    )

    placed = {
        "host_id": "host_alice",
        "workspace": "/Users/alice/globalhub",
        "report_new_sessions": False,
    }
    assert saved.json() == placed
    assert (await client.get(GLOBALHUB_ROUTE)).json() == placed
    assert [
        (validation["host_id"], validation["workspace"], validation["spec_cwd"])
        for validation in validations
    ] == [("host_alice", "/Users/alice/./globalhub", None)]

    # A request without the switch leaves it as it is.
    cleared = await client.put(GLOBALHUB_ROUTE, json=GLOBALHUB_UNSET)
    unset_and_off = {**GLOBALHUB_DEFAULTS, "report_new_sessions": False}
    assert cleared.json() == unset_and_off
    assert (await client.get(GLOBALHUB_ROUTE)).json() == unset_and_off
    assert len(validations) == 1


async def test_custodian_profile_saves_its_schedule_and_refuses_a_bad_one(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_task_roles, "now_epoch", lambda: 1_800_000_000)
    schedule = {
        "enabled": True,
        "rrule": "FREQ=DAILY;BYHOUR=18;BYMINUTE=30",
        "timezone": "Europe/Berlin",
    }
    unsaved = (await client.get(CUSTODIAN_PROFILE_ROUTE)).json()

    saved = await client.put(CUSTODIAN_PROFILE_ROUTE, json={"schedule": schedule})
    off = await client.put(
        CUSTODIAN_PROFILE_ROUTE, json={"schedule": {**schedule, "enabled": False}}
    )
    refused = [
        await client.put(
            CUSTODIAN_PROFILE_ROUTE,
            json={"description": "Changed.", "schedule": {**schedule, **bad}},
        )
        for bad in (
            {"timezone": "Mars/Olympus_Mons"},
            {"rrule": "FREQ=MINUTELY;INTERVAL=5"},
            {"rrule": "FREQ=DAILY;BYHOUR=9;COUNT=3"},
        )
    ]

    assert (unsaved["schedulable"], unsaved["schedule"]) == (True, None)
    # Now is 08:00 UTC on 2027-01-15, a winter day, so 18:30 in Berlin is 17:30 UTC.
    assert saved.json()["schedule"] == {**schedule, "next_run_at": 1_800_034_200}
    turned_off = {**schedule, "enabled": False, "next_run_at": None}
    assert off.json()["schedule"] == turned_off
    # A refused schedule changes nothing, also not the description it carries.
    assert [response.status_code for response in refused] == [400, 400, 400]
    stored = (await client.get(CUSTODIAN_PROFILE_ROUTE)).json()
    assert (stored["schedule"], stored["customized"]) == (turned_off, [])

    deleted = await client.put(CUSTODIAN_PROFILE_ROUTE, json={"schedule": None})
    assert deleted.json()["schedule"] is None


async def test_the_first_placement_turns_on_the_custodians_sweep_and_adds_the_default_watchers(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def canonical_workspace(**_kwargs: object) -> str:
        return "/Users/alice/globalhub"

    monkeypatch.setattr(
        user_settings, "validate_uploaded_bundle_host_workspace", canonical_workspace
    )
    monkeypatch.setattr(user_settings, "now_epoch", lambda: 1_800_000_000)
    placement = {"host_id": "host_alice", "workspace": "/Users/alice/globalhub"}

    refused = await client.put(
        GLOBALHUB_ROUTE, json={**placement, "timezone": "Mars/Olympus_Mons"}
    )
    first = await client.put(GLOBALHUB_ROUTE, json={**placement, "timezone": "America/New_York"})
    started = (await client.get(CUSTODIAN_PROFILE_ROUTE)).json()["schedule"]
    watchers = (await client.get(WATCHERS_ROUTE)).json()["data"]
    default_watchers = [watcher for watcher in watchers if not watcher["builtin"]]
    await client.delete(f"{WATCHERS_ROUTE}/{default_watchers[0]['id']}")
    off = {"enabled": False, "rrule": started["rrule"], "timezone": started["timezone"]}
    await client.put(CUSTODIAN_PROFILE_ROUTE, json={"schedule": off})
    await client.put(GLOBALHUB_ROUTE, json=GLOBALHUB_UNSET)
    await client.put(GLOBALHUB_ROUTE, json={**placement, "timezone": "UTC"})
    kept = (await client.get(CUSTODIAN_PROFILE_ROUTE)).json()["schedule"]
    watchers_after_placing_again = [
        watcher
        for watcher in (await client.get(WATCHERS_ROUTE)).json()["data"]
        if not watcher["builtin"]
    ]

    assert refused.status_code == 400
    assert first.status_code == 200, first.text
    assert [
        (watcher["name"], watcher["schedule"]["next_run_at"])
        for watcher in watchers
        if watcher["builtin"]
    ] == [("Fallback watcher", 1_800_001_800)]
    assert [
        (watcher["name"], watcher["customized"], watcher["schedule"]["rrule"])
        for watcher in default_watchers
    ] == [
        ("GitHub watcher", ["manual"], "FREQ=MINUTELY;INTERVAL=30"),
        ("Slack watcher", ["manual"], "FREQ=MINUTELY;INTERVAL=30"),
        ("Jira watcher", ["manual"], "FREQ=MINUTELY;INTERVAL=30"),
    ]
    assert [watcher["schedule"]["next_run_at"] for watcher in default_watchers] == [
        1_800_001_800,
        1_800_001_800,
        1_800_001_800,
    ]
    assert [watcher["name"] for watcher in watchers_after_placing_again] == [
        "Slack watcher",
        "Jira watcher",
    ]
    # Now is 08:00 UTC on 2027-01-15, a winter day, so 09:00 in New York is 14:00 UTC.
    assert started == {
        "enabled": True,
        "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        "timezone": "America/New_York",
        "next_run_at": 1_800_021_600,
    }
    # A sweep the user turned off stays off when the hub is placed again.
    assert kept == {**off, "next_run_at": None}


async def test_a_role_without_a_schedule_refuses_one(client: httpx.AsyncClient) -> None:
    schedule = {"enabled": True, "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "timezone": "UTC"}

    response = await client.put(MANAGER_PROFILE_ROUTE, json={"schedule": schedule})

    assert response.status_code == 400
    profile = (await client.get(MANAGER_PROFILE_ROUTE)).json()
    assert (profile["schedulable"], profile["schedule"]) == (False, None)


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
    assert alice.json() == GLOBALHUB_DEFAULTS


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
    # The role's sessions start with this model, so it gets a session override's check.
    unsafe = await client.put(MANAGER_PROFILE_ROUTE, json={"model": "example; rm -rf /"})

    assert saved.status_code == 200, saved.text
    assert (saved.json()["manual"], saved.json()["model"], saved.json()["customized"]) == (
        "Route nothing.",
        "example-model",
        ["model", "manual"],
    )
    # A reset needs the defaults even while the fields are overridden.
    assert (saved.json()["default_manual"], saved.json()["default_description"]) == (
        default["manual"],
        default["description"],
    )
    assert unsafe.status_code == 400

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


async def test_placements_start_the_custodian_session_that_the_role_route_reuses(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, db_uri: str
) -> None:
    host_id = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    unplaced = await client.post(CUSTODIAN_SESSION_ROUTE)

    async def canonical_workspace(**kwargs: object) -> str:
        return str(kwargs["workspace"])

    monkeypatch.setattr(
        user_settings, "validate_uploaded_bundle_host_workspace", canonical_workspace
    )
    # The custodian's agent is not installed yet, so only the placement saves.
    placed = await client.put(
        GLOBALHUB_ROUTE, json={"host_id": host_id, "workspace": "/Users/alice/globalhub"}
    )
    SqlAlchemyAgentStore(db_uri).create(
        uuid.uuid4().hex, "onih-custodian", "bundles/onih-custodian.tar.gz"
    )
    first = await client.post(CUSTODIAN_SESSION_ROUTE)
    second = await client.post(CUSTODIAN_SESSION_ROUTE)
    await client.put(GLOBALHUB_ROUTE, json={"host_id": host_id, "workspace": "/Users/alice/hub"})
    moved = await client.post(CUSTODIAN_SESSION_ROUTE)
    manager = await client.post("/v1/agent-tasks/roles/manager:default/session")

    assert unplaced.status_code == 400
    assert placed.status_code == 200, placed.text
    assert first.status_code == 200, first.text
    assert (first.json()["created"], second.json()["created"]) == (True, False)
    assert second.json()["session_id"] == first.json()["session_id"]
    # The move started a new custodian at the new workspace before the route asked.
    assert moved.json()["created"] is False
    assert moved.json()["session_id"] != first.json()["session_id"]
    assert manager.status_code == 400
    session = SqlAlchemyConversationStore(db_uri).get_conversation(moved.json()["session_id"])
    assert session is not None
    assert (session.title, session.labels[ROLE_LABEL], session.host_id, session.workspace) == (
        "Task custodian",
        CUSTODIAN_ROLE_VALUE,
        host_id,
        "/Users/alice/hub",
    )


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
    assert (await multi_user_client.post(CUSTODIAN_SESSION_ROUTE)).status_code == 401
