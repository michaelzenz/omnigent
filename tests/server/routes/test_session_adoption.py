"""Route tests for session adoption — simplified direct-adopt flow."""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_AGENT_NAME, resolve_task_agent_id
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.worker_store import WORKER_KIND_EXTERNAL
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
async def task_manager_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


@pytest_asyncio.fixture()
def conversation_store(db_uri: str) -> SqlAlchemyConversationStore:
    return SqlAlchemyConversationStore(db_uri)


@pytest_asyncio.fixture()
def worker_store(db_uri: str) -> SqlAlchemyWorkerStore:
    return SqlAlchemyWorkerStore(db_uri)


async def test_adopt_session_directly(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
    conversation_store: SqlAlchemyConversationStore,
    worker_store: SqlAlchemyWorkerStore,
    db_uri: str,
) -> None:
    """Direct adopt creates a Worker + human_action item — no proposal step."""
    _seed_live_host(db_uri, "host_test")
    conv = conversation_store.create_conversation(
        title="Upload retries",
        agent_id=task_manager_agent_id,
    )

    task_resp = await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Upload retries",
            "goal": "all uploads retry to success",
        },
    )
    task_id = task_resp.json()["id"]

    adopt_resp = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_id},
    )
    assert adopt_resp.status_code == 200, adopt_resp.text
    body = adopt_resp.json()
    assert body["object"] == "agent.task.session_adoption"
    assert body["session_id"] == conv.id
    assert body["task_id"] == task_id
    assert body["worker_id"] is not None
    assert body["item_id"] is not None

    worker = worker_store.get_by_target_id(conv.id)
    assert worker is not None
    assert worker.task_id == task_id
    assert worker.kind == WORKER_KIND_EXTERNAL
