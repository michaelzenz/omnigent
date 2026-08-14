"""Tests for session watcher Phase 1 — event types, worker hint lookup,
ingress auto-routing, purge with event_type filter, and the update endpoint.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest

from omnigent.agent_tasks.event_types import (
    EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
    EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
    is_session_internal_event,
)
from omnigent.agent_tasks.role_keys import WORKER_DEFAULT_ROLE_KEY
from omnigent.db.utils import generate_agent_id
from omnigent.entities.agent_queue import AgentQueueKey
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store import WORKER_KIND_EXTERNAL
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


# ── Event types ──────────────────────────────────────────────────────


def test_external_event_types_are_not_session_internal() -> None:
    """External session events must pass through the ingress guard."""
    assert not is_session_internal_event(EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE)
    assert not is_session_internal_event(EXTERNAL_SESSION_UPDATED_EVENT_TYPE)


def test_external_event_type_constants() -> None:
    assert EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE == "external.session.discovered"
    assert EXTERNAL_SESSION_UPDATED_EVENT_TYPE == "external.session.updated"


# ── Worker store: get_by_external_hint ──────────────────────────────


def test_get_by_external_hint_finds_external_worker(db_uri: str) -> None:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)

    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="ext-agent", bundle_location="test:///b")
    conv = conv_store.create_conversation(
        title="Ext", agent_id=agent_id, host_id=_uid("h"), workspace="/tmp"
    )
    task_id = _uid("task_hint")
    task_store.create(task_id, "Hint task", manager_conversation_id=conv.id)

    hint = "codex-session-abc123"
    worker = worker_store.create_worker(
        _uid("worker_hint"),
        task_id,
        kind=WORKER_KIND_EXTERNAL,
        agent_profile_id=agent_id,
        session_id=conv.id,
    )
    # The create_worker API doesn't set external_session_hint yet;
    # update it directly via the SQLAlchemy session for the test.
    from sqlalchemy import update as sa_update

    from omnigent.db.db_models import SqlWorker, current_workspace_id
    from omnigent.db.utils import get_or_create_engine, make_managed_session_maker

    engine = get_or_create_engine(db_uri)
    session_maker = make_managed_session_maker(engine)
    with session_maker() as session:
        session.execute(
            sa_update(SqlWorker)
            .where(SqlWorker.workspace_id == current_workspace_id())
            .where(SqlWorker.id == worker.id)
            .values(external_session_hint=hint)
        )
        session.flush()

    found = worker_store.get_by_external_hint(hint)
    assert found is not None
    assert found.id == worker.id
    assert found.task_id == task_id


def test_get_by_external_hint_returns_none_for_unknown(db_uri: str) -> None:
    worker_store = SqlAlchemyWorkerStore(db_uri)
    assert worker_store.get_by_external_hint("nonexistent-hint") is None


# ── purge_old_events with event_type filter ─────────────────────────


def test_purge_old_events_with_event_type_filter(db_uri: str) -> None:
    event_store = SqlAlchemyTaskEventStore(db_uri)
    now = int(time.time())

    # Create two routed events of different types.
    evt_a = event_store.create_event(
        uuid.uuid4().hex,
        EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
        "Discovered A",
        state="routed",
    )
    evt_b = event_store.create_event(
        uuid.uuid4().hex,
        EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
        "Updated B",
        state="routed",
    )

    # Purge only the discovered events — updated should survive.
    n = event_store.purge_old_events(
        before_ts=now + 10_000,
        states=["routed"],
        event_type=EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
    )
    assert n == 1
    assert event_store.get_event(evt_a.id) is None
    assert event_store.get_event(evt_b.id) is not None

    # Purge updated events too.
    n = event_store.purge_old_events(
        before_ts=now + 10_000,
        states=["routed"],
        event_type=EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
    )
    assert n == 1
    assert event_store.get_event(evt_b.id) is None


def test_purge_old_events_without_event_type_purges_all_types(db_uri: str) -> None:
    event_store = SqlAlchemyTaskEventStore(db_uri)
    now = int(time.time())

    evt_a = event_store.create_event(
        uuid.uuid4().hex,
        EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
        "Discovered A",
        state="routed",
    )
    evt_b = event_store.create_event(
        uuid.uuid4().hex,
        EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
        "Updated B",
        state="routed",
    )

    n = event_store.purge_old_events(
        before_ts=now + 10_000,
        states=["routed"],
    )
    assert n == 2
    assert event_store.get_event(evt_a.id) is None
    assert event_store.get_event(evt_b.id) is None


# ── Ingress auto-routing for external.session.updated ───────────────


def _role_profile(agent_profile_id: str, *, host_seed: str, workspace: str):
    from omnigent.agent_tasks.agent_builtins import TASK_BROKER_ROLE
    from omnigent.entities.task_role_profile import TaskRoleProfile

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


def _mock_session_creator(conversation_store):
    async def _creator(*, body, request, user_id, **kwargs):
        return conversation_store.create_conversation(
            title=body.title or "Task manager",
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
        )

    return _creator


@pytest.mark.asyncio
async def test_ingress_auto_routes_external_session_updated_by_hint(
    db_uri: str,
) -> None:
    """An external.session.updated event with a known hint auto-routes to the task."""
    from types import SimpleNamespace

    from omnigent.agent_tasks.ingress import ingress_event
    from sqlalchemy import update as sa_update

    from omnigent.db.db_models import SqlWorker, current_workspace_id
    from omnigent.db.utils import get_or_create_engine, make_managed_session_maker

    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)

    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="ext-agent", bundle_location="test:///b")
    mgr_conv = conv_store.create_conversation(
        title="Mgr", agent_id=agent_id, host_id=_uid("hm"), workspace="/tmp"
    )
    task_id = _uid("task_route")
    task_store.create(task_id, "Route task", manager_conversation_id=mgr_conv.id)

    worker_conv = conv_store.create_conversation(
        kind="sub_agent",
        title="Ext worker",
        parent_conversation_id=mgr_conv.id,
        agent_id=agent_id,
        host_id=_uid("hw"),
        workspace="/tmp",
    )
    worker = worker_store.create_worker(
        _uid("worker_route"),
        task_id,
        kind=WORKER_KIND_EXTERNAL,
        agent_profile_id=agent_id,
        session_id=worker_conv.id,
    )
    hint = "codex-session-route-test"
    engine = get_or_create_engine(db_uri)
    session_maker = make_managed_session_maker(engine)
    with session_maker() as session:
        session.execute(
            sa_update(SqlWorker)
            .where(SqlWorker.workspace_id == current_workspace_id())
            .where(SqlWorker.id == worker.id)
            .values(external_session_hint=hint)
        )
        session.flush()

    payload = json.dumps({"session_hint": hint, "transcript_delta": "new work"})
    event = event_store.create_event(
        uuid.uuid4().hex,
        EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
        "External session update",
        payload=payload,
        source="session_watcher",
        source_key=hint,
        state="received",
    )

    profile = _role_profile(agent_id, host_seed="host_route", workspace="/tmp/route")
    distributed = await ingress_event(
        event=event,
        task_store=task_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conv_store,
        role_profile=profile,
        session_creator=_mock_session_creator(conv_store),
        app_state=SimpleNamespace(),
    )
    assert distributed.state == "routed"
    assert distributed.task_id == task_id


@pytest.mark.asyncio
async def test_ingress_stalls_external_session_updated_unknown_hint(
    db_uri: str,
) -> None:
    """An external.session.updated event with an unknown hint stalls."""
    from omnigent.agent_tasks.ingress import ingress_event

    event_store = SqlAlchemyTaskEventStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)

    hint = "unknown-hint-xyz"
    payload = json.dumps({"session_hint": hint, "transcript_delta": "work"})
    event = event_store.create_event(
        uuid.uuid4().hex,
        EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
        "External session update",
        payload=payload,
        source="session_watcher",
        source_key=hint,
        state="received",
    )

    distributed = await ingress_event(
        event=event,
        task_store=task_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conv_store,
    )
    assert distributed.state == "awaiting_grouping"
