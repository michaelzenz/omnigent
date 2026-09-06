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
        json={"task_id": task_id},
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
    assert worker.kind == WORKER_KIND_EXTERNAL
