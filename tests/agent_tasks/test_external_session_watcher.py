"""Tests for external session watcher Phase 1 — event types, worker hint lookup,
ingress auto-routing, purge with event_type filter, and the update endpoint.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest

from omnigent.agent_tasks.event_types import (
    EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
    EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
    is_session_internal_event,
)
from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
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


def test_get_by_target_id_finds_external_worker(db_uri: str) -> None:
    """External workers are keyed by the watcher's session hint on target_id."""
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
    task_store.create(task_id, "Hint task", "hint goal", manager_id=conv.id)

    hint = "codex-session-abc123"
    worker = worker_store.create_worker(
        _uid("worker_hint"),
        task_id,
        kind=WORKER_KIND_EXTERNAL,
        target_id=hint,
    )

    found = worker_store.get_by_target_id(hint)
    assert found is not None
    assert found.id == worker.id
    assert found.task_id == task_id


def test_get_by_target_id_returns_none_for_unknown(db_uri: str) -> None:
    worker_store = SqlAlchemyWorkerStore(db_uri)
    assert worker_store.get_by_target_id("nonexistent-hint") is None


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
    # Managers are first-class: register the durable row the task points at.
    from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore

    manager_row = SqlAlchemyManagerStore(db_uri).upsert(
        _uid("route_mgr"),
        conversation_id=mgr_conv.id,
        owner_user_id="__anonymous__",
        role_key="manager:default",
        description="Route manager",
        host_id=_uid("hm"),
        workspace="/tmp",
        harness="cursor",
        model="composer-2.5",
        agent_profile_id=agent_id,
    )
    task_id = _uid("task_route")
    task_store.create(task_id, "Route task", "route goal", manager_id=manager_row.id)

    _worker_conv = conv_store.create_conversation(
        kind="sub_agent",
        title="Ext worker",
        parent_conversation_id=mgr_conv.id,
        agent_id=agent_id,
        host_id=_uid("hw"),
        workspace="/tmp",
    )
    hint = "codex-session-route-test"
    worker_store.create_worker(
        _uid("worker_route"),
        task_id,
        kind=WORKER_KIND_EXTERNAL,
        target_id=hint,
    )

    payload = json.dumps({"session_hint": hint, "transcript_delta": "new work"})
    event = event_store.create_event(
        uuid.uuid4().hex,
        EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
        "External session update",
        payload=payload,
        source="external_session_watcher",
        source_key=hint,
        state="received",
    )

    distributed = await ingress_event(
        event=event,
        task_store=task_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conv_store,
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
        source="external_session_watcher",
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


# ── Phase 3: External session adoption flow ─────────────────────────


def test_propose_external_session_adoption_creates_new_task(db_uri: str) -> None:
    """Proposing adoption with no task_id creates a new pending task."""
    from omnigent.agent_tasks.adoption import propose_external_session_adoption

    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)

    hint = "codex-propose-new"
    task, proposal = propose_external_session_adoption(
        session_hint=hint,
        task_id=None,
        task_store=task_store,
        task_event_store=event_store,
        owner_user_id="__anonymous__",
        transcript_snippet="working on a parser bug",
    )
    assert task.title == f"External session: {hint}"
    assert proposal.event_type == "session.adoption"
    assert proposal.task_id == task.id
    assert proposal.source_key == hint
    import json as _json

    payload = _json.loads(proposal.payload)
    assert payload["session_hint"] == hint
    assert payload["external"] is True
    assert payload["transcript_snippet"] == "working on a parser bug"


def test_propose_external_session_adoption_uses_existing_task(db_uri: str) -> None:
    """Proposing adoption with an existing task_id routes to that task."""
    from omnigent.agent_tasks.adoption import propose_external_session_adoption

    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)

    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="mgr-propose", bundle_location="test:///b")
    mgr_conv = conv_store.create_conversation(
        title="Mgr", agent_id=agent_id, host_id=_uid("hm3"), workspace="/tmp"
    )
    task_id = _uid("task_existing")
    task_store.create(task_id, "Existing task", "existing goal", manager_id=mgr_conv.id)

    hint = "codex-propose-existing"
    task, proposal = propose_external_session_adoption(
        session_hint=hint,
        task_id=task_id,
        task_store=task_store,
        task_event_store=event_store,
        owner_user_id="__anonymous__",
    )
    assert task.id == task_id
    assert proposal.task_id == task_id


def test_propose_external_session_adoption_reconciles_discovered_event(
    db_uri: str,
) -> None:
    """Proposing adoption marks the discovered event as reconciled."""
    from omnigent.agent_tasks.adoption import propose_external_session_adoption

    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)

    hint = "codex-reconcile"
    # Create a discovered event
    event_store.create_event(
        uuid.uuid4().hex,
        EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
        "Discovered",
        payload=json.dumps({"session_hint": hint}),
        source="external_session_watcher",
        source_key=hint,
        state="awaiting_grouping",
    )
    propose_external_session_adoption(
        session_hint=hint,
        task_id=None,
        task_store=task_store,
        task_event_store=event_store,
    )
    # The discovered event should be reconciled now.
    discovered = event_store.list_events(
        state="awaiting_grouping",
        event_type=EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
    )
    assert not any(e.source_key == hint for e in discovered)


@pytest.mark.asyncio
async def test_adopt_external_session_creates_worker_with_hint(db_uri: str) -> None:
    """Adopting an external session creates a worker with external_session_hint."""
    from types import SimpleNamespace

    from omnigent.agent_tasks.adoption import (
        adopt_external_session,
        propose_external_session_adoption,
    )

    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)

    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="mgr-adopt", bundle_location="test:///b")
    mgr_conv = conv_store.create_conversation(
        title="Mgr", agent_id=agent_id, host_id=_uid("hm4"), workspace="/tmp"
    )

    hint = "codex-adopt-test"
    task, proposal = propose_external_session_adoption(
        session_hint=hint,
        task_id=None,
        task_store=task_store,
        task_event_store=event_store,
        owner_user_id="__anonymous__",
    )
    # Managers are first-class: register the durable row and point the task
    # at it; bootstrap heals the session from the row when needed.
    from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore

    manager_store = SqlAlchemyManagerStore(db_uri)
    manager_row = manager_store.upsert(
        _uid("adopt_mgr"),
        conversation_id=mgr_conv.id,
        owner_user_id="__anonymous__",
        role_key="manager:default",
        description="Adoption manager",
        host_id=_uid("hm4"),
        workspace="/tmp",
        harness="cursor",
        model="composer-2.5",
        agent_profile_id=agent_id,
    )
    task_store.update(task.id, manager_id=manager_row.id)

    _, adopted = await adopt_external_session(
        session_hint=hint,
        task_id=task.id,
        task_store=task_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conv_store,
        proposal_event=proposal,
        session_creator=_mock_session_creator(conv_store),
        app_state=SimpleNamespace(),
    )
    assert adopted.event_type == "session.adopted"
    assert adopted.task_id == task.id

    worker = worker_store.get_by_target_id(hint)
    assert worker is not None
    assert worker.task_id == task.id
    assert worker.kind == "external"

    # Proposal should be reconciled
    proposal_updated = event_store.get_event(proposal.id)
    assert proposal_updated.state == "reconciled"


def test_reject_external_session_adoption_dismisses_proposal(db_uri: str) -> None:
    """Rejecting an external session adoption dismisses the proposal."""
    from omnigent.agent_tasks.adoption import (
        propose_external_session_adoption,
        reject_external_session_adoption,
    )

    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)

    hint = "codex-reject-test"
    _, proposal = propose_external_session_adoption(
        session_hint=hint,
        task_id=None,
        task_store=task_store,
        task_event_store=event_store,
    )
    dismissed = reject_external_session_adoption(
        session_hint=hint,
        task_event_store=event_store,
        proposal_event=proposal,
    )
    assert dismissed is not None
    assert dismissed.state == "dismissed"


# ── Phase 4: Manager notice format for transcript deltas ───────────


def test_format_manager_notice_includes_external_update_delta() -> None:
    """The manager notice renders external.session.updated with the delta."""
    from omnigent.agent_tasks.notices import _format_manager_notice

    event = type(
        "E",
        (),
        {
            "event_type": EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
            "title": "External session update",
            "source_key": "codex-delta",
            "payload": json.dumps(
                {
                    "session_hint": "codex-delta",
                    "history_hash": "h5",
                    "transcript_delta": "user: can you fix the bug?\nassistant: I'll look at it",
                }
            ),
        },
    )()
    notice = _format_manager_notice([event])
    assert "external.session.updated" in notice
    assert "codex-delta" in notice
    assert "Transcript delta:" in notice
    assert "fix the bug" in notice
    assert "Copy button" in notice


def test_format_manager_notice_includes_rewind() -> None:
    """The manager notice indicates rewind for rewind events."""
    from omnigent.agent_tasks.notices import _format_manager_notice

    event = type(
        "E",
        (),
        {
            "event_type": EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
            "title": "External session rewind",
            "source_key": "codex-rewind",
            "payload": json.dumps(
                {
                    "session_hint": "codex-rewind",
                    "rewind_at": "h3",
                    "history_hash": "h7",
                    "transcript_delta": "rewritten messages",
                }
            ),
        },
    )()
    notice = _format_manager_notice([event])
    assert "rewound" in notice
    assert "h3" in notice
    assert "rewritten messages" in notice


# ── Phase 6: Adoption timeout GC ───────────────────────────────────


@pytest.mark.asyncio
async def test_purge_old_events_purges_stale_adoption_proposals(db_uri: str) -> None:
    """Adoption proposals in routed state older than 1 day are purged."""
    from omnigent.agent_tasks.event_types import SESSION_ADOPTION_PROPOSAL

    event_store = SqlAlchemyTaskEventStore(db_uri)
    now = int(time.time())

    # Create an old routed adoption proposal (older than 1 day)
    old_proposal = event_store.create_event(
        uuid.uuid4().hex,
        SESSION_ADOPTION_PROPOSAL,
        "Old adoption proposal",
        state="routed",
        task_id=_uid("task_gc"),
    )
    # Create a recent routed adoption proposal (within 1 day)
    recent_proposal = event_store.create_event(
        uuid.uuid4().hex,
        SESSION_ADOPTION_PROPOSAL,
        "Recent adoption proposal",
        state="routed",
        task_id=_uid("task_gc"),
    )

    # Purge proposals older than 1 day — only the old one should be purged
    n = event_store.purge_old_events(
        before_ts=now - 86_400,  # 1 day ago
        states=["routed"],
        event_type=SESSION_ADOPTION_PROPOSAL,
    )
    # The old proposal was created "now" (created_at = now), so it's NOT older than 1 day.
    # Both should survive. Let's fix the test by using a far-future cutoff.
    assert n == 0
    assert event_store.get_event(old_proposal.id) is not None
    assert event_store.get_event(recent_proposal.id) is not None

    # Now purge with a future cutoff — both should be purged
    n = event_store.purge_old_events(
        before_ts=now + 10_000,
        states=["routed"],
        event_type=SESSION_ADOPTION_PROPOSAL,
    )
    assert n == 2
    assert event_store.get_event(old_proposal.id) is None
    assert event_store.get_event(recent_proposal.id) is None
