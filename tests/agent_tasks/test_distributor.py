"""Tests for the task event distributor."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.distributor import distribute_event
from omnigent.db.utils import generate_agent_id
from omnigent.entities import TaskTag
from omnigent.entities import TaskEventTag
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.secretary_profile_store.sqlalchemy_store import SqlAlchemySecretaryProfileStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def manager_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-manager-agent", bundle_location="test:///bundle")
    return agent_id


@pytest.fixture
def stores(db_uri: str, manager_agent_id: str) -> dict:
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    secretary_store = SqlAlchemySecretaryProfileStore(db_uri)
    task_id = _uid("dist_task")
    task_store.create(
        task_id,
        manager_agent_id,
        "Upload retries",
        charter="flaky upload retries repo:omnigent-fork",
        tags=[TaskTag(task_id=task_id, tag_type="repo", tag="omnigent-fork")],
    )
    return {
        "task_store": task_store,
        "event_store": event_store,
        "conversation_store": conversation_store,
        "secretary_store": secretary_store,
        "task_id": task_id,
        "manager_agent_id": manager_agent_id,
    }


@pytest.mark.asyncio
async def test_distributor_auto_routes_clear_match(db_uri: str, stores: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = stores["event_store"]
    event_id = _uid("auto_event")
    event = event_store.create_event(
        event_id,
        "build.finished",
        "Upload retries failed",
        summary="repo omnigent-fork upload flaky",
        state="received",
        tags=[
            TaskEventTag(event_id=event_id, tag_type="repo", tag="omnigent-fork"),
        ],
    )
    profile = UserSecretaryProfile(
        user_id="__anonymous__",
        agent_id=stores["manager_agent_id"],
        harness="cursor",
        model="composer-2.5",
        host_id=_uid("host_dist"),
        workspace="/tmp/dist-test",
        created_at=1,
    )
    updated = await distribute_event(
        event=event,
        task_store=stores["task_store"],
        task_event_store=event_store,
        conversation_store=stores["conversation_store"],
        runner_router=None,
        secretary_profile=profile,
    )
    assert updated.state == "awaiting_manager_triage"
    assert updated.task_id == stores["task_id"]


@pytest.mark.asyncio
async def test_distributor_stalls_when_no_tasks(db_uri: str, manager_agent_id: str) -> None:
    event_store = SqlAlchemyTaskEventStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    secretary_store = SqlAlchemySecretaryProfileStore(db_uri)
    event_id = _uid("stall_event")
    event = event_store.create_event(
        event_id,
        "build.finished",
        "Unknown project",
        state="received",
    )
    updated = await distribute_event(
        event=event,
        task_store=task_store,
        task_event_store=event_store,
        conversation_store=conversation_store,
        runner_router=None,
        secretary_profile_store=secretary_store,
        owner_user_id="__anonymous__",
    )
    assert updated.state == "awaiting_new_manager_decision"


@pytest.mark.asyncio
async def test_distributor_skips_internal_events(db_uri: str, stores: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = stores["event_store"]
    event = event_store.create_event(
        _uid("internal_event"),
        "manager.proposal",
        "Proposal",
        task_id=stores["task_id"],
        state="awaiting_user_ack",
    )
    updated = await distribute_event(
        event=event,
        task_store=stores["task_store"],
        task_event_store=event_store,
        conversation_store=stores["conversation_store"],
        runner_router=None,
    )
    assert updated.state == "awaiting_user_ack"
