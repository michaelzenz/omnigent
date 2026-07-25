"""Route tests for secretary task-item routing proposals."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
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


async def test_routing_proposal_board_and_accept(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    worker_agent_id: str,
    db_uri: str,
) -> None:
    """Create a routing proposal, list it on the board, and accept it."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    task_id = _uid("routing-task")
    task_store.create(task_id, manager_agent_id, "omnigent-fork", charter="repo:omnigent-fork")

    event_id = _uid("routing-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        summary="repo:omnigent-fork pr:891",
    )

    bootstrap = await client.post(
        f"/v1/agent-tasks/{task_id}/bootstrap",
        json={
            "host_id": _uid("host"),
            "workspace": "/tmp/omnigent-task-test",
            "harness": "cursor-native",
            "model": "composer-2.5",
        },
    )
    assert bootstrap.status_code == 200

    created = await client.post(
        "/v1/task-items/routing-proposals",
        json={
            "canonical_key": "pr:omnigent-fork#891",
            "title": "Fix PR 891",
            "event_ids": [event_id],
            "recommended_task_id": task_id,
            "instructions": "Investigate CI failure",
            "worker_agent_id": worker_agent_id,
            "host_id": _uid("host"),
            "workspace": "/tmp/omnigent-task-test",
            "harness": "cursor-native",
            "model": "composer-2.5",
            "rationale": "Matches omnigent-fork charter",
        },
    )
    assert created.status_code == 200
    item_id = created.json()["id"]
    assert created.json()["state"] == "routing_proposed"

    board = await client.get("/v1/agent-tasks/board/decisions")
    assert board.status_code == 200
    cards = board.json()["data"]
    assert any(card["id"] == item_id for card in cards)

    accepted = await client.post(
        f"/v1/task-items/{item_id}/resolve-routing",
        json={"resolution": "accept_routing", "selected_task_id": task_id},
    )
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "running"
    assert accepted.json().get("execution_id")

    event = event_store.get_event(event_id)
    assert event is not None
    assert event.state == "reconciled"


async def test_routing_proposal_accept_with_edited_instructions(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    worker_agent_id: str,
    db_uri: str,
) -> None:
    """Accepting a routing proposal can override instructions before dispatch."""
    from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore

    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    task_id = _uid("routing-task-edited")
    task_store.create(task_id, manager_agent_id, "omnigent-fork", charter="repo:omnigent-fork")

    event_id = _uid("routing-event-edited")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        summary="repo:omnigent-fork pr:902",
    )

    bootstrap = await client.post(
        f"/v1/agent-tasks/{task_id}/bootstrap",
        json={
            "host_id": _uid("host-edited"),
            "workspace": "/tmp/omnigent-task-test",
            "harness": "cursor-native",
            "model": "composer-2.5",
        },
    )
    assert bootstrap.status_code == 200

    created = await client.post(
        "/v1/task-items/routing-proposals",
        json={
            "canonical_key": "pr:omnigent-fork#902",
            "title": "Fix PR 902",
            "event_ids": [event_id],
            "recommended_task_id": task_id,
            "instructions": "Original instructions",
            "worker_agent_id": worker_agent_id,
            "host_id": _uid("host-edited"),
            "workspace": "/tmp/omnigent-task-test",
            "harness": "cursor-native",
            "model": "composer-2.5",
        },
    )
    assert created.status_code == 200
    item_id = created.json()["id"]

    accepted = await client.post(
        f"/v1/task-items/{item_id}/resolve-routing",
        json={
            "resolution": "accept_routing",
            "selected_task_id": task_id,
            "instructions": "Edited before dispatch",
        },
    )
    assert accepted.status_code == 200

    item = item_store.get_item(item_id)
    assert item is not None
    assert item.instructions == "Edited before dispatch"
