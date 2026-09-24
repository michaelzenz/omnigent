"""Route tests for session adoption — simplified direct-adopt flow."""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio

from omnigent.db.utils import generate_agent_id
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.worker_store import WORKER_KIND_INTERNAL
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore
from tests.server.routes.agent_task_api import patch_host_session_launch


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _seed_live_host(db_uri: str, seed: str) -> str:
    host_id = _uid(seed)
    HostStore(db_uri).upsert_on_connect(host_id, seed, RESERVED_USER_LOCAL)
    return host_id


@pytest.fixture(autouse=True)
def _patch_host_session_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_host_session_launch(monkeypatch)


@pytest_asyncio.fixture()
async def manager_agent_id(db_uri: str) -> str:
    """Register a standalone agent the adopted session can carry."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="adoption-manager", bundle_location="test:///bundle")
    return agent_id


@pytest_asyncio.fixture()
async def manager_id(db_uri: str, manager_agent_id: str) -> str:
    """Register the first-class manager the test's task attaches to."""
    import uuid as _uuid

    from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore

    def _uid(seed: str) -> str:
        return _uuid.uuid5(_uuid.NAMESPACE_DNS, seed).hex

    host_id = _uid("adoption-mgr-host")
    manager_conversation_id = _uid("adoption-mgr-conv")
    conversation_store = SqlAlchemyConversationStore(db_uri)
    conversation_store.create_conversation(
        conversation_id=manager_conversation_id,
        title="Adoption manager",
        agent_id=manager_agent_id,
        host_id=host_id,
        workspace="/tmp/adoption-manager",
    )
    manager_row_id = _uid("adoption-manager-row")
    SqlAlchemyManagerStore(db_uri).upsert(
        manager_row_id,
        conversation_id=manager_conversation_id,
        owner_user_id="__anonymous__",
        role_key="manager:default",
        description="Owns adopted work.",
        host_id=host_id,
        workspace="/tmp/adoption-manager",
        harness="cursor",
        model="composer-2.5",
        agent_profile_id=manager_agent_id,
    )
    return manager_row_id


@pytest_asyncio.fixture()
def conversation_store(db_uri: str) -> SqlAlchemyConversationStore:
    return SqlAlchemyConversationStore(db_uri)


@pytest_asyncio.fixture()
def worker_store(db_uri: str) -> SqlAlchemyWorkerStore:
    return SqlAlchemyWorkerStore(db_uri)


async def test_adopt_session_directly(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    manager_id: str,
    conversation_store: SqlAlchemyConversationStore,
    worker_store: SqlAlchemyWorkerStore,
    db_uri: str,
) -> None:
    """Direct adopt creates a Worker + human_action item — no proposal step."""
    _seed_live_host(db_uri, "host_test")
    conv = conversation_store.create_conversation(
        title="Upload retries",
        agent_id=manager_agent_id,
    )

    task_resp = await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Upload retries",
            "goal": "all uploads retry to success",
            "manager_id": manager_id,
        },
    )
    assert task_resp.status_code == 200, task_resp.text
    task_id = task_resp.json()["id"]

    adopt_resp = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_id, "title": "Investigating upload retries"},
    )
    assert adopt_resp.status_code == 200, adopt_resp.text
    body = adopt_resp.json()
    assert body["object"] == "agent.task.session_adoption"
    assert body["session_id"] == conv.id
    assert body["task_id"] == task_id
    assert body["worker_id"] is not None

    worker = worker_store.get_by_target_id(conv.id)
    assert worker is not None
    assert worker.task_id == task_id
    assert worker.kind == WORKER_KIND_INTERNAL
    assert worker.title == "Investigating upload retries"

    readopt_resp = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_id, "title": "Verifying upload retry fix"},
    )
    assert readopt_resp.status_code == 200, readopt_resp.text
    assert readopt_resp.json()["worker_id"] == worker.id
    assert readopt_resp.json()["already_bound"] is True

    updated_worker = worker_store.get_worker(worker.id)
    assert updated_worker is not None
    assert updated_worker.title == "Verifying upload retry fix"

    listed = await client.get(f"/v1/agent-tasks/{task_id}/workers")
    assert listed.status_code == 200, listed.text
    listed_worker = next(
        candidate for candidate in listed.json()["data"] if candidate["worker_id"] == worker.id
    )
    assert listed_worker["title"] == "Verifying upload retry fix"

    blank_title = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_id, "title": "   "},
    )
    assert blank_title.status_code == 422


async def _create_task(client: httpx.AsyncClient, manager_id: str, title: str) -> str:
    resp = await client.post(
        "/v1/agent-tasks",
        json={"title": title, "goal": title, "manager_id": manager_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def test_adopt_session_second_task_update_does_not_duplicate(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    manager_id: str,
    conversation_store: SqlAlchemyConversationStore,
    worker_store: SqlAlchemyWorkerStore,
    db_uri: str,
) -> None:
    """Re-adopting a session for a task whose lane is not the session's
    oldest worker must update that task's lane, not create a duplicate."""
    _seed_live_host(db_uri, "host_test")
    conv = conversation_store.create_conversation(
        title="Shared session",
        agent_id=manager_agent_id,
    )
    task_a = await _create_task(client, manager_id, "Task A")
    task_b = await _create_task(client, manager_id, "Task B")

    # Task A binds first, so its lane is the session's oldest worker row.
    first_a = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_a, "title": "Working on A"},
    )
    assert first_a.status_code == 200, first_a.text

    first_b = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_b, "title": "Working on B"},
    )
    assert first_b.status_code == 200, first_b.text
    worker_b = first_b.json()["worker_id"]

    # Manager updates the task-B lane: the same worker row must be reused.
    update_b = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_b, "title": "Still on B"},
    )
    assert update_b.status_code == 200, update_b.text
    assert update_b.json()["worker_id"] == worker_b
    assert update_b.json()["already_bound"] is True

    bound_b = [
        w
        for w in worker_store.list_workers_by_target_id(conv.id)
        if w.task_id == task_b and w.state != "deleted"
    ]
    assert len(bound_b) == 1
    assert bound_b[0].title == "Still on B"


async def test_adopt_session_same_task_repeated_is_single_worker(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    manager_id: str,
    conversation_store: SqlAlchemyConversationStore,
    worker_store: SqlAlchemyWorkerStore,
    db_uri: str,
) -> None:
    """Repeated adoption of one session to one task yields exactly one worker."""
    _seed_live_host(db_uri, "host_test")
    conv = conversation_store.create_conversation(
        title="Repeated adoption",
        agent_id=manager_agent_id,
    )
    task_id = await _create_task(client, manager_id, "Single lane")

    worker_ids: list[str] = []
    for round_index in range(3):
        resp = await client.post(
            f"/v1/agent-tasks/sessions/{conv.id}/adopt",
            json={"task_id": task_id, "title": f"Round {round_index}"},
        )
        assert resp.status_code == 200, resp.text
        worker_ids.append(resp.json()["worker_id"])

    assert len(set(worker_ids)) == 1
    assert len(worker_store.list_workers_for_task(task_id)) == 1


async def test_adopt_session_after_untrack_resurrects_lane(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    manager_id: str,
    conversation_store: SqlAlchemyConversationStore,
    worker_store: SqlAlchemyWorkerStore,
    db_uri: str,
) -> None:
    """Re-adoption after the user untracked the lane revives the same row."""
    _seed_live_host(db_uri, "host_test")
    conv = conversation_store.create_conversation(
        title="Resurrect me",
        agent_id=manager_agent_id,
    )
    task_id = await _create_task(client, manager_id, "Resurrect")

    adopt = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_id, "title": "First pass"},
    )
    worker_id = adopt.json()["worker_id"]

    untrack = await client.post(f"/v1/task-workers/{worker_id}/untrack")
    assert untrack.status_code == 200, untrack.text
    assert worker_store.get_worker(worker_id).state == "terminated"

    readopt = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_id, "title": "Second pass"},
    )
    assert readopt.status_code == 200, readopt.text
    assert readopt.json()["worker_id"] == worker_id

    revived = worker_store.get_worker(worker_id)
    assert revived.state == "idle"
    assert revived.title == "Second pass"
