"""Tests for the task event ingress."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.agent_builtins import TASK_BROKER_ROLE, TASK_MANAGER_AGENT_NAME
from omnigent.agent_tasks.ingress import ingress_event
from omnigent.db.utils import generate_agent_id
from omnigent.entities import EventTag, TaskTag
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_role_profile_store.sqlalchemy_store import SqlAlchemyTaskRoleProfileStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _ensure_agent(agent_store: SqlAlchemyAgentStore, agent_id: str, name: str) -> str:
    existing = agent_store.get_by_name(name)
    if existing is not None:
        return existing.id
    agent_store.create(agent_id, name=name, bundle_location="test:///bundle")
    return agent_id


@pytest.fixture
def manager_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    return _ensure_agent(agent_store, agent_id, TASK_MANAGER_AGENT_NAME)


def _role_profile(agent_profile_id: str, *, host_seed: str, workspace: str) -> TaskRoleProfile:
    return TaskRoleProfile(
        role=TASK_BROKER_ROLE,
        kind="broker",
        agent_profile_id=agent_profile_id,
        harness="cursor",
        model="composer-2.5",
        host_id=_uid(host_seed),
        workspace=workspace,
        created_at=1,
    )


@pytest.fixture
def stores(db_uri: str, manager_agent_id: str) -> dict:
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    secretary_store = SqlAlchemyTaskRoleProfileStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    task_id = _uid("ingress_task")
    task_store.create(
        task_id,
        "Upload retries",
        internal_note="flaky upload retries repo:omnigent-fork",
        tags=[TaskTag(task_id=task_id, tag_type="repo", tag="omnigent-fork")],
    )
    return {
        "task_store": task_store,
        "event_store": event_store,
        "conversation_store": conversation_store,
        "secretary_store": secretary_store,
        "worker_store": worker_store,
        "task_id": task_id,
        "agent_profile_id": manager_agent_id,
    }


@pytest.mark.asyncio
async def test_ingress_auto_routes_clear_match(db_uri: str, stores: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = stores["event_store"]
    event_id = _uid("auto_event")
    event = event_store.create_event(
        event_id,
        "build.finished",
        "Upload retries failed",
        state="received",
        tags=[
            EventTag(tag_type="repo", tag="omnigent-fork"),
        ],
    )
    profile = _role_profile(
        stores["agent_profile_id"],
        host_seed="host_ingress",
        workspace="/tmp/ingress-test",
    )
    updated = await ingress_event(
        event=event,
        task_store=stores["task_store"],
        task_event_store=event_store,
        worker_store=stores["worker_store"],
        conversation_store=stores["conversation_store"],
        role_profile=profile,
    )
    assert updated.state == "routed"
    assert updated.task_id == stores["task_id"]
    attempts = event_store.list_routing_attempts(event_id)
    assert len(attempts) == 1
    assert attempts[0].candidate_task_id == stores["task_id"]
    assert attempts[0].reason == "auto-route score=1.0000"


@pytest.mark.asyncio
async def test_ingress_stalls_when_no_tasks(db_uri: str, manager_agent_id: str) -> None:
    event_store = SqlAlchemyTaskEventStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    secretary_store = SqlAlchemyTaskRoleProfileStore(db_uri)
    event_id = _uid("stall_event")
    event = event_store.create_event(
        event_id,
        "build.finished",
        "Unknown project",
        state="received",
    )
    updated = await ingress_event(
        event=event,
        task_store=task_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        task_role_profile_store=secretary_store,
        owner_user_id="__anonymous__",
    )
    assert updated.state == "awaiting_grouping"


@pytest.mark.asyncio
async def test_ingress_skips_session_internal_events(db_uri: str, stores: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = stores["event_store"]
    event = event_store.create_event(
        _uid("internal_event"),
        "session.adoption",
        "Adoption proposal",
        source_internal_session_id=_uid("orphan_session"),
        state="received",
    )
    updated = await ingress_event(
        event=event,
        task_store=stores["task_store"],
        task_event_store=event_store,
        worker_store=stores["worker_store"],
        conversation_store=stores["conversation_store"],
    )
    assert updated.state == "received"


@pytest.mark.asyncio
async def test_ingress_fast_paths_explicit_task_id(db_uri: str, stores: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = stores["event_store"]
    event_id = _uid("bound_event")
    event = event_store.create_event(
        event_id,
        "github.pr.merged",
        "Blocker PR merged",
        task_id=stores["task_id"],
        state="received",
    )
    profile = _role_profile(
        stores["agent_profile_id"],
        host_seed="host_bound",
        workspace="/tmp/ingress-bound",
    )
    updated = await ingress_event(
        event=event,
        task_store=stores["task_store"],
        task_event_store=event_store,
        worker_store=stores["worker_store"],
        conversation_store=stores["conversation_store"],
        role_profile=profile,
    )
    assert updated.state == "routed"
    assert updated.task_id == stores["task_id"]
    attempts = event_store.list_routing_attempts(event_id)
    assert len(attempts) == 1
    assert attempts[0].reason == "explicit-task"
