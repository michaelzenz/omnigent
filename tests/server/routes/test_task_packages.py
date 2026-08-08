"""Route tests for pending task packages."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_AGENT_NAME, resolve_task_agent_id
from omnigent.db.utils import generate_agent_id
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


@pytest_asyncio.fixture()
async def worker_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-worker-agent", bundle_location="test:///bundle")
    return agent_id


@pytest_asyncio.fixture()
async def worker_role_key(client: httpx.AsyncClient, worker_agent_id: str) -> str:
    """Register the worker role a resolved package item is handed to."""
    resp = await client.post(
        "/v1/agent-tasks/roles/worker",
        json={"slug": "packager", "agent_profile_id": worker_agent_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["role"]


def _seed_live_host(db_uri: str, seed: str) -> str:
    host_id = _uid(seed)
    HostStore(db_uri).upsert_on_connect(host_id, seed, RESERVED_USER_LOCAL)
    return host_id


def _bootstrap_body() -> dict[str, str]:
    return {
        "host_id": _uid("host_test"),
        "workspace": "/tmp/omnigent-task-test",
        "harness": "cursor",
        "model": "composer-2.5",
    }


async def test_create_task_package_lists_as_paused_task(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    db_uri: str,
) -> None:
    """Create a pending package and surface it in the pending task list."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    event_id = _uid("package-route-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
    )

    created = await client.post(
        "/v1/agent-tasks/packages",
        json={
            "title": "CI failure on PR #123",
            "items": [
                {
                    "title": "Investigate CI failure",
                    "event_ids": [event_id],
                    "instructions": "Check workflow logs",
                },
            ],
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["state"] == "pending"
    task_id = body["id"]

    listed = await client.get("/v1/agent-tasks", params={"state": "pending", "limit": 100})
    assert listed.status_code == 200
    pending = listed.json()["data"]
    assert any(task["id"] == task_id for task in pending)

    event = event_store.get_event(event_id)
    assert event is not None
    assert event.state == "reconciled"


async def test_resolve_inbox_item_activates_paused_package(
    client: httpx.AsyncClient,
    worker_role_key: str,
    db_uri: str,
) -> None:
    """Go on a pending package inbox item activates the task and dispatches a worker."""
    _seed_live_host(db_uri, "package-resolve-host")
    event_store = SqlAlchemyTaskEventStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_id = _uid("resolve-route-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
    )

    created = await client.post(
        "/v1/agent-tasks/packages",
        json={
            "title": "Package to activate",
            "items": [
                {"title": "Do work", "event_ids": [event_id], "instructions": "Do the work"},
            ],
        },
    )
    assert created.status_code == 200
    task_id = created.json()["id"]

    item_store = SqlAlchemyTaskItemStore(db_uri)
    item = item_store.list_items_for_task(task_id, state="awaiting_user_ack")[0]

    resolved = await client.post(
        f"/v1/task-items/{item.id}/resolve",
        json={
            "resolution": "edit_and_dispatch",
            "edited_payload": {
                "worker_role_key": worker_role_key,
                **_bootstrap_body(),
            },
        },
    )
    assert resolved.status_code == 200, resolved.text
    # Phase 4: accept no longer launches a worker synchronously — the item
    # moves to ``queued`` and the dispatcher spawns the worker off the path.
    assert resolved.json()["state"] == "queued"
    assert resolved.json().get("execution_id") is None

    activated = task_store.get(task_id)
    assert activated is not None
    # The task leaves ``pending`` (activated) but has no running worker yet, so
    # it sits idle with a queued backlog rather than going active.
    assert activated.state == "idle"
    assert activated.manager_conversation_id is not None


async def test_skip_inbox_items_keeps_paused_task(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    db_uri: str,
) -> None:
    """Skipping all inbox items leaves the pending package on the board."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_ids = [_uid("skip-route-e1"), _uid("skip-route-e2")]
    for event_id in event_ids:
        event_store.create_event(
            event_id,
            "github.pr.checks_failed",
            "PR checks failed",
            state="awaiting_grouping",
        )

    created = await client.post(
        "/v1/agent-tasks/packages",
        json={
            "title": "Package to skip",
            "items": [
                {"title": "Skip me", "event_ids": [event_ids[0]]},
                {"title": "Skip me too", "event_ids": [event_ids[1]]},
            ],
        },
    )
    assert created.status_code == 200
    task_id = created.json()["id"]

    for item in item_store.list_items_for_task(task_id, state="awaiting_user_ack"):
        skipped = await client.post(
            f"/v1/task-items/{item.id}/resolve",
            json={"resolution": "reject_item"},
        )
        assert skipped.status_code == 200
        assert skipped.json()["state"] == "cancelled"

    task = task_store.get(task_id)
    assert task is not None
    assert task.state == "pending"


async def test_reconcile_events_extends_package_item(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    db_uri: str,
) -> None:
    """POST reconcile-events attaches ambiguous events to a pending package item."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    first_event = _uid("reconcile-event-1")
    second_event = _uid("reconcile-event-2")
    for event_id in (first_event, second_event):
        event_store.create_event(
            event_id,
            "build.finished",
            "Flaky upload",
            state="awaiting_grouping",
        )

    package = await client.post(
        "/v1/agent-tasks/packages",
        json={
            "title": "Upload retries",
            "items": [
                {
                    "title": "First failure",
                    "event_ids": [first_event],
                    "instructions": "Investigate",
                },
            ],
        },
    )
    assert package.status_code == 200
    task_id = package.json()["id"]
    item_id = item_store.list_items_for_task(task_id, state="awaiting_user_ack")[0].id

    reconciled = await client.post(
        f"/v1/agent-tasks/{task_id}/reconcile-events",
        json={
            "title": "First failure",
            "event_ids": [second_event],
            "description": "Same upload path failed again",
            "instructions": "Investigate both failures together",
            "internal_note": "linked to first inbox item",
            "item_id": item_id,
        },
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["object"] == "list"
    assert reconciled.json()["data"][0]["id"] == item_id
    assert event_store.get_event(second_event) is not None
    assert event_store.get_event(second_event).state == "reconciled"
    links = item_store.list_events_for_item(item_id)
    assert {link.event_id for link in links} == {first_event, second_event}


async def test_reconcile_events_batch_creates_multiple_items(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    db_uri: str,
) -> None:
    """POST reconcile-events with `items` reconciles multiple items in one call."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    events = [_uid(f"batch-event-{i}") for i in range(4)]
    for event_id in events:
        event_store.create_event(
            event_id,
            "build.finished",
            "Flaky upload",
            state="awaiting_grouping",
        )

    package = await client.post(
        "/v1/agent-tasks/packages",
        json={
            "title": "Upload retries",
            "items": [
                {"title": "Seed item", "event_ids": [events[0]]},
            ],
        },
    )
    assert package.status_code == 200
    task_id = package.json()["id"]

    # One call, two new items — one fresh and one extending the seed item.
    seed_item_id = item_store.list_items_for_task(task_id, state="awaiting_user_ack")[0].id
    reconciled = await client.post(
        f"/v1/agent-tasks/{task_id}/reconcile-events",
        json={
            "items": [
                {"title": "Second failure", "event_ids": [events[1], events[2]]},
                {
                    "title": "Seed item",
                    "event_ids": [events[3]],
                    "item_id": seed_item_id,
                },
            ],
        },
    )
    assert reconciled.status_code == 200
    body = reconciled.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 2
    # Every event is now reconciled exactly once.
    for event_id in events:
        assert event_store.get_event(event_id).state == "reconciled"
    # The seed item now carries both its original and the newly attached event.
    seed_links = {link.event_id for link in item_store.list_events_for_item(seed_item_id)}
    assert seed_links == {events[0], events[3]}
