"""Route tests for orphan session adoption (Phase 5.5)."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.agent_tasks.session_labels import ROUTING_SEARCH_TEXT_LABEL
from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-manager-agent", bundle_location="test:///bundle")
    return agent_id


@pytest_asyncio.fixture()
def conversation_store(db_uri: str) -> SqlAlchemyConversationStore:
    return SqlAlchemyConversationStore(db_uri)


@pytest_asyncio.fixture()
def task_event_store(db_uri: str) -> SqlAlchemyTaskEventStore:
    return SqlAlchemyTaskEventStore(db_uri)


def _bootstrap_body() -> dict[str, str]:
    return {
        "host_id": _uid("host_test"),
        "workspace": "/tmp/omnigent-adoption-test",
        "harness": "cursor",
        "model": "composer-2.5",
    }


async def test_session_adoption_flow(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    conversation_store: SqlAlchemyConversationStore,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    """Propose, adopt, and bind an orphan session to a task."""
    conv = conversation_store.create_conversation(
        title="Upload retries",
        agent_id=manager_agent_id,
    )
    conversation_store.set_labels(
        conv.id,
        {ROUTING_SEARCH_TEXT_LABEL: "upload retries flaky CI pipeline"},
    )

    task_resp = await client.post(
        "/v1/agent-tasks",
        json={"manager_agent_id": manager_agent_id, "title": "Upload retries"},
    )
    task_id = task_resp.json()["id"]

    propose_resp = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/propose-adoption",
    )
    assert propose_resp.status_code == 200
    proposal = propose_resp.json()
    assert proposal["event_type"] == "session.adoption"
    assert proposal["state"] == "awaiting_user_ack"
    assert proposal["source_session_id"] == conv.id

    adopt_resp = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/adopt",
        json={"task_id": task_id, **_bootstrap_body()},
    )
    assert adopt_resp.status_code == 200
    body = adopt_resp.json()
    assert body["binding_kind"] == "ambient"
    assert body["task_id"] == task_id
    assert body["event"]["event_type"] == "session.adopted"
    assert body["event"]["state"] == "awaiting_manager_triage"
    assert body["proposal"]["state"] == "processed"

    binding = task_event_store.get_binding(conv.id)
    assert binding is not None
    assert binding.task_id == task_id


async def test_reject_session_adoption(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    conversation_store: SqlAlchemyConversationStore,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    """Rejecting adoption dismisses the proposal and leaves the session orphan."""
    conv = conversation_store.create_conversation(
        title="Stay orphan",
        agent_id=manager_agent_id,
    )
    conversation_store.set_labels(conv.id, {ROUTING_SEARCH_TEXT_LABEL: "misc cleanup"})

    propose_resp = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/propose-adoption",
    )
    assert propose_resp.status_code == 200

    reject_resp = await client.post(
        f"/v1/agent-tasks/sessions/{conv.id}/reject-adoption",
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["proposal"]["state"] == "dismissed"
    assert task_event_store.get_binding(conv.id) is None

    updated = conversation_store.get_conversation(conv.id)
    assert updated is not None
    assert updated.labels.get("omnigent.task.adoption_dismissed") == "1"
