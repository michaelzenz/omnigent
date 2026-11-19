"""Tests for the GlobalHub watcher routes (``/v1/agent-tasks/watchers``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from omnigent.agent_tasks.roles import WATCHER_ROLE
from omnigent.agent_tasks.session_labels import ROLE_LABEL, WATCHER_ROLE_VALUE
from omnigent.entities import SESSION_EVENT_SOURCE
from omnigent.server.routes import agent_task_watchers
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.role_schedule_store.sqlalchemy_store import SqlAlchemyRoleScheduleStore
from omnigent.stores.subscription_store.sqlalchemy_store import SqlAlchemySubscriptionStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE_ID = "alice@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": "bob@example.com"}
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
ROUTE = "/v1/agent-tasks/watchers"
PULL_REQUEST = "github.pull_request"
TICKET = "jira.issue"
# 08:00 UTC on 2027-01-15, a winter day, when Berlin is at UTC+1.
NOW = 1_800_000_000
EVERY_10_MINUTES = "FREQ=MINUTELY;INTERVAL=10"
HOURLY = {"enabled": True, "rrule": "FREQ=HOURLY;BYMINUTE=30", "timezone": "Europe/Berlin"}


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_task_watchers, "now_epoch", lambda: NOW)


async def _create(
    client: httpx.AsyncClient, name: str = "PR watcher", **fields: Any
) -> dict[str, Any]:
    response = await client.post(ROUTE, json={"name": name, **fields}, headers=ALICE)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_new_watcher_starts_from_the_template_and_runs_every_10_minutes(
    auth_client: httpx.AsyncClient,
) -> None:
    defaults = (await auth_client.get(f"{ROUTE}/defaults", headers=ALICE)).json()

    watcher = await _create(auth_client, description="  Pull requests on my tasks  ")

    template = WATCHER_ROLE.default_manual()
    assert template.startswith("# Watcher manual")
    assert (defaults["manual"], defaults["rrule"]) == (template, EVERY_10_MINUTES)
    assert (watcher["name"], watcher["description"], watcher["manual"]) == (
        "PR watcher",
        "Pull requests on my tasks",
        template,
    )
    assert (watcher["customized"], watcher["model"], watcher["session_id"]) == ([], None, None)
    assert (watcher["builtin"], watcher["source_types"]) == (False, [])
    # Every 10 minutes from midnight, so the first run after 08:00 is 08:10.
    assert watcher["schedule"] == {
        "enabled": True,
        "rrule": EVERY_10_MINUTES,
        "timezone": "UTC",
        "next_run_at": NOW + 600,
    }


async def test_a_watcher_keeps_its_own_manual_model_and_schedule(
    auth_client: httpx.AsyncClient,
) -> None:
    watcher = await _create(
        auth_client, manual="Watch the release.", model="example-model", schedule=HOURLY
    )

    detail = (await auth_client.get(f"{ROUTE}/{watcher['id']}", headers=ALICE)).json()
    listed = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]

    assert (detail["manual"], detail["customized"], detail["model"]) == (
        "Watch the release.",
        ["manual"],
        "example-model",
    )
    assert detail["default_manual"] == WATCHER_ROLE.default_manual()
    # 08:00 UTC is 09:00 in Berlin, so the next run is 09:30 there, 08:30 UTC.
    assert detail["schedule"]["next_run_at"] == NOW + 1_800
    assert [(row["id"], "manual" in row) for row in listed if not row["builtin"]] == [
        (watcher["id"], False)
    ]


async def test_an_update_changes_only_the_fields_it_names(auth_client: httpx.AsyncClient) -> None:
    watcher = await _create(auth_client, manual="Watch the release.", model="example-model")
    path = f"{ROUTE}/{watcher['id']}"

    renamed = await auth_client.patch(
        path, json={"name": " Release watcher ", "description": "Releases"}, headers=ALICE
    )
    reset = await auth_client.patch(path, json={"manual": None, "model": "  "}, headers=ALICE)
    off = await auth_client.patch(
        path, json={"schedule": {**HOURLY, "enabled": False}}, headers=ALICE
    )
    no_schedule = await auth_client.patch(path, json={"schedule": None}, headers=ALICE)

    assert (renamed.json()["name"], renamed.json()["description"], renamed.json()["manual"]) == (
        "Release watcher",
        "Releases",
        "Watch the release.",
    )
    assert (reset.json()["manual"], reset.json()["customized"], reset.json()["model"]) == (
        WATCHER_ROLE.default_manual(),
        [],
        None,
    )
    assert off.json()["schedule"] == {**HOURLY, "enabled": False, "next_run_at": None}
    assert (off.json()["name"], no_schedule.json()["schedule"]) == ("Release watcher", None)


@pytest.mark.parametrize(
    "change",
    [
        {"schedule": {**HOURLY, "rrule": "FREQ=MINUTELY;INTERVAL=5"}},
        {"schedule": {**HOURLY, "timezone": "Mars/Olympus_Mons"}},
        {"schedule": {**HOURLY, "rrule": "FREQ=HOURLY;COUNT=3"}},
        {"model": "example; rm -rf /"},
    ],
)
async def test_a_refused_update_changes_nothing(
    auth_client: httpx.AsyncClient, change: dict[str, Any]
) -> None:
    watcher = await _create(auth_client)
    path = f"{ROUTE}/{watcher['id']}"

    response = await auth_client.patch(path, json={"name": "Renamed", **change}, headers=ALICE)

    assert response.status_code == 400
    assert (await auth_client.get(path, headers=ALICE)).json() == watcher


async def test_create_refuses_a_bad_watcher_before_saving_anything(
    auth_client: httpx.AsyncClient,
) -> None:
    blank = await auth_client.post(ROUTE, json={"name": "  "}, headers=ALICE)
    extra = await auth_client.post(ROUTE, json={"name": "x", "owner": "bob"}, headers=ALICE)
    too_often = await auth_client.post(
        ROUTE,
        json={"name": "x", "schedule": {**HOURLY, "rrule": "FREQ=MINUTELY;INTERVAL=5"}},
        headers=ALICE,
    )

    assert (blank.status_code, extra.status_code, too_often.status_code) == (422, 422, 400)
    listed = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]
    assert [row["builtin"] for row in listed] == [True]


async def test_delete_removes_the_watcher_and_its_schedule(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    watcher = await _create(auth_client)
    other = await _create(auth_client, "Ticket watcher")

    deleted = await auth_client.delete(f"{ROUTE}/{watcher['id']}", headers=ALICE)
    again = await auth_client.delete(f"{ROUTE}/{watcher['id']}", headers=ALICE)

    assert deleted.json() == {
        "object": "agent.task.watcher",
        "id": watcher["id"],
        "deleted": True,
        "conversation_id": None,
    }
    assert again.status_code == 404
    schedules = SqlAlchemyRoleScheduleStore(db_uri)
    assert schedules.get(f"watcher:{watcher['id']}", owner_user_id=ALICE_ID) is None
    assert schedules.get(f"watcher:{other['id']}", owner_user_id=ALICE_ID) is not None
    listed = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]
    assert [row["id"] for row in listed if not row["builtin"]] == [other["id"]]


async def test_watchers_are_private_to_each_user(auth_client: httpx.AsyncClient) -> None:
    watcher = await _create(auth_client)
    path = f"{ROUTE}/{watcher['id']}"

    bobs = (await auth_client.get(ROUTE, headers=BOB)).json()["data"]
    responses = [
        await auth_client.get(path, headers=BOB),
        await auth_client.patch(path, json={"name": "Mine"}, headers=BOB),
        await auth_client.delete(path, headers=BOB),
        await auth_client.post(f"{path}/session", headers=BOB),
    ]

    assert [(row["name"], row["builtin"]) for row in bobs] == [("Fallback watcher", True)]
    assert [response.status_code for response in responses] == [404, 404, 404, 404]
    assert (await auth_client.get(path, headers=ALICE)).json() == watcher


async def test_the_session_route_creates_then_reuses_the_watchers_session(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    SqlAlchemyAgentStore(db_uri).create(
        uuid.uuid4().hex, "onih-watcher", "bundles/onih-watcher.tar.gz"
    )
    watcher = await _create(auth_client, model="example-model")
    path = f"{ROUTE}/{watcher['id']}/session"
    unplaced = await auth_client.post(path, headers=ALICE)
    UserSettingsStore(db_uri).save_globalhub_placement(
        GlobalHubPlacement(host_id=HOST_ID, workspace="/Users/alice/globalhub"),
        user_id=ALICE_ID,
    )

    first = await auth_client.post(path, headers=ALICE)
    second = await auth_client.post(path, headers=ALICE)

    assert unplaced.status_code == 400
    assert (first.json()["created"], second.json()["created"]) == (True, False)
    assert second.json()["session_id"] == first.json()["session_id"]
    session = SqlAlchemyConversationStore(db_uri).get_conversation(first.json()["session_id"])
    assert session is not None
    assert (session.title, session.labels[ROLE_LABEL], session.host_id) == (
        "PR watcher",
        WATCHER_ROLE_VALUE,
        HOST_ID,
    )
    assert session.model_override == "example-model"
    detail = (await auth_client.get(f"{ROUTE}/{watcher['id']}", headers=ALICE)).json()
    assert detail["session_id"] == first.json()["session_id"]


async def test_the_fallback_watcher_checks_the_types_no_watcher_covers_and_stays(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    [fallback] = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]
    watcher = await _create(auth_client)
    subscriptions = SqlAlchemySubscriptionStore(db_uri)
    for source_type, source_key in (
        (PULL_REQUEST, "https://github.com/example-org/repo/pull/12"),
        (TICKET, "PROJ-12"),
        (SESSION_EVENT_SOURCE, uuid.uuid4().hex),
    ):
        subscriptions.subscribe(
            owner_user_id=ALICE_ID,
            session_id=uuid.uuid4().hex,
            source_type=source_type,
            source_key=source_key,
        )
    subscriptions.assign_type(
        owner_user_id=ALICE_ID, source_type=PULL_REQUEST, watcher_id=watcher["id"]
    )
    path = f"{ROUTE}/{fallback['id']}"

    listed = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]
    detail = (await auth_client.get(path, headers=ALICE)).json()
    refused = await auth_client.delete(path, headers=ALICE)
    await auth_client.delete(f"{ROUTE}/{watcher['id']}", headers=ALICE)
    after_delete = (await auth_client.get(path, headers=ALICE)).json()

    assert (fallback["name"], fallback["builtin"], fallback["source_types"]) == (
        "Fallback watcher",
        True,
        [],
    )
    assert fallback["schedule"] == {
        "enabled": True,
        "rrule": EVERY_10_MINUTES,
        "timezone": "UTC",
        "next_run_at": NOW + 600,
    }
    assert {row["id"]: row["source_types"] for row in listed} == {
        fallback["id"]: [TICKET],
        watcher["id"]: [PULL_REQUEST],
    }
    assert detail["default_manual"].startswith("# Watcher manual")
    assert "You are the built-in fallback watcher." in detail["default_manual"]
    assert (detail["manual"], detail["customized"], detail["source_types"]) == (
        detail["default_manual"],
        [],
        [TICKET],
    )
    assert refused.status_code == 409
    assert after_delete["source_types"] == [PULL_REQUEST, TICKET]


async def test_creating_or_deleting_a_watcher_asks_the_custodian_to_assign_source_types(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    SqlAlchemyAgentStore(db_uri).create(
        uuid.uuid4().hex, "onih-custodian", "bundles/onih-custodian.tar.gz"
    )
    settings = UserSettingsStore(db_uri)
    settings.save_globalhub_placement(
        GlobalHubPlacement(host_id=HOST_ID, workspace="/Users/alice/globalhub"),
        user_id=ALICE_ID,
    )
    queue = SqlAlchemyAgentQueueStore(db_uri)

    watcher = await _create(auth_client)
    after_create = queue.list(owner_user_id=ALICE_ID)
    for item in after_create:
        queue.delete(item.id, owner_user_id=ALICE_ID)
    await auth_client.delete(f"{ROUTE}/{watcher['id']}", headers=ALICE)
    after_delete = queue.list(owner_user_id=ALICE_ID)

    custodian = settings.get_globalhub_role_session("custodian", user_id=ALICE_ID)
    assert [(item.kind, item.target_session_id) for item in after_create] == [
        ("source_types", custodian)
    ]
    assert [(item.kind, item.target_session_id) for item in after_delete] == [
        ("source_types", custodian)
    ]
