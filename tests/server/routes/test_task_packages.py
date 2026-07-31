"""Route tests for pending task packages."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-manager-agent", bundle_location="test:///bundle")
    return agent_id


@pytest_asyncio.fixture()
async def worker_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-worker-agent", bundle_location="test:///bundle")
    return agent_id


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
        summary="repo:acme/widgets pr:123",
    )

    created = await client.post(
        "/v1/agent-tasks/packages",
        json={
            "title": "CI failure on PR #123",
            "manager_agent_id": manager_agent_id,
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
    manager_agent_id: str,
    worker_agent_id: str,
    db_uri: str,
) -> None:
    """Go on a pending package inbox item activates the task and dispatches a worker."""
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
            "manager_agent_id": manager_agent_id,
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
                "worker_agent_id": worker_agent_id,
                **_bootstrap_body(),
            },
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["state"] == "running"
    assert resolved.json()["execution_id"] is not None

    activated = task_store.get(task_id)
    assert activated is not None
    assert activated.state == "active"
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
            "manager_agent_id": manager_agent_id,
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


async def test_match_tasks_includes_paused_task(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    db_uri: str,
) -> None:
    """Pending tasks are eligible match candidates."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    paused_id = _uid("paused-route-task")
    task_store.create(
        paused_id,
        manager_agent_id,
        "omnigent-fork",
        state="pending",
        charter="repo:omnigent-fork",
    )
    event_id = _uid("match-route-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        summary="repo:omnigent-fork pr:891",
    )

    matched = await client.post(
        "/v1/task-events/match-tasks",
        json={"event_ids": [event_id]},
    )
    assert matched.status_code == 200
    candidates = matched.json()["candidates"]
    assert candidates
    assert candidates[0]["task_id"] == paused_id
    assert candidates[0]["state"] == "pending"
