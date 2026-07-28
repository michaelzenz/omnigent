"""Route tests for paused task packages."""

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


async def test_create_task_package_and_list_pending(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    db_uri: str,
) -> None:
    """Create a paused package and surface it on the pending board."""
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
    assert body["state"] == "paused"
    task_id = body["id"]

    board = await client.get("/v1/agent-tasks/board/pending")
    assert board.status_code == 200
    pending = board.json()["pending"]
    assert any(card["id"] == task_id for card in pending)

    event = event_store.get_event(event_id)
    assert event is not None
    assert event.state == "reconciled"


async def test_accept_task_package(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    db_uri: str,
) -> None:
    """Accepting a package activates the task and approves inbox items."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_id = _uid("accept-route-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
    )

    created = await client.post(
        "/v1/agent-tasks/packages",
        json={
            "title": "Package to accept",
            "manager_agent_id": manager_agent_id,
            "items": [{"title": "Do work", "event_ids": [event_id]}],
        },
    )
    assert created.status_code == 200
    task_id = created.json()["id"]

    accepted = await client.post(
        f"/v1/agent-tasks/{task_id}/accept-package",
        json={
            "host_id": _uid("host"),
            "workspace": "/tmp/omnigent-task-test",
            "harness": "cursor-native",
            "model": "composer-2.5",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "active"

    items = item_store.list_items_for_task(task_id, state="approved")
    assert len(items) == 1


async def test_match_tasks_includes_paused_task(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    db_uri: str,
) -> None:
    """Paused tasks are eligible match candidates."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    paused_id = _uid("paused-route-task")
    task_store.create(
        paused_id,
        manager_agent_id,
        "omnigent-fork",
        state="paused",
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
    assert candidates[0]["state"] == "paused"
