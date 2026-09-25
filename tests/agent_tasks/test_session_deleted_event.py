"""Tests for the ``session.deleted`` task event — emit paths and manager notice.

Mirrors the ``session.turn.finished`` event: a bound session broadcasts a
born-``routed`` event to its governing managers; an unbound session emits
only when one of its turn-finished events is already in flight, riding the
same lane (routed to the holding manager, or awaiting broker grouping).
"""

from __future__ import annotations

import json
import uuid

import pytest

from omnigent.agent_tasks import adoption
from omnigent.agent_tasks.adoption import (
    SessionAdoptionContext,
    configure_session_adoption,
    emit_session_deleted_event,
    emit_session_deleted_event_for_pending_turn,
)
from omnigent.agent_tasks.event_types import (
    SESSION_DELETED_EVENT_TYPE,
    SESSION_TURN_FINISHED_EVENT_TYPE,
    is_ingress_candidate,
    is_session_internal_event,
)
from omnigent.agent_tasks.notices import (
    _format_manager_notice,
    _format_session_batch_notice,
)
from omnigent.entities.conversation import Conversation
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


# ── Event type lane classification ──────────────────────────────────


def test_session_deleted_event_type_constant() -> None:
    assert SESSION_DELETED_EVENT_TYPE == "session.deleted"


def test_session_deleted_event_is_session_internal() -> None:
    """A ``session.*`` event stays in the internal lane (never broker ingress)."""
    assert is_session_internal_event(SESSION_DELETED_EVENT_TYPE) is True
    assert is_ingress_candidate(SESSION_DELETED_EVENT_TYPE) is False


# ── Emit: bound session broadcast ───────────────────────────────────


@pytest.fixture()
def adoption_ctx(db_uri: str) -> SessionAdoptionContext:
    ctx = SessionAdoptionContext(
        task_store=SqlAlchemyTaskStore(db_uri),
        task_event_store=SqlAlchemyTaskEventStore(db_uri),
        worker_store=SqlAlchemyWorkerStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        task_item_store=SqlAlchemyTaskItemStore(db_uri),
        manager_store=SqlAlchemyManagerStore(db_uri),
    )
    configure_session_adoption(ctx)
    yield ctx
    configure_session_adoption(None)


def _seed_bound_session(
    db_uri: str,
    ctx: SessionAdoptionContext,
    *,
    session_id: str,
    manager_id: str | None = None,
    owner_user_id: str | None = "alice",
    task_state: str = "active",
) -> str:
    """Create a manager, a live task under it, and a worker bound to the session."""
    manager_id = manager_id or _uid(f"mgr-{session_id}")
    ctx.manager_store.upsert(
        manager_id,
        owner_user_id=owner_user_id,
        role_key="manager:default",
        description="test manager",
        conversation_id=_uid("mgr-conv"),
    )
    task = ctx.task_store.create(
        _uid(f"task-{session_id}"),
        "Build the thing",
        "goal",
        manager_id=manager_id,
        owner_user_id=owner_user_id,
        state=task_state,
    )
    ctx.worker_store.create_worker(_uid(f"worker-{session_id}"), task.id, target_id=session_id)
    return manager_id


def _pre_delete_conv(session_id: str, title: str = "My working session") -> Conversation:
    """A pre-delete conversation snapshot, as the delete endpoint holds it."""
    return Conversation(
        id=session_id,
        created_at=0,
        updated_at=0,
        root_conversation_id=session_id,
        title=title,
    )


def test_emit_session_deleted_broadcasts_to_governing_manager(
    db_uri: str, adoption_ctx: SessionAdoptionContext
) -> None:
    session_id = _uid("session-a")
    manager_id = _seed_bound_session(db_uri, adoption_ctx, session_id=session_id)

    emit_session_deleted_event(session_id=session_id, conv=_pre_delete_conv(session_id))

    events = adoption_ctx.task_event_store.list_events(
        event_type=SESSION_DELETED_EVENT_TYPE, state="routed"
    )
    assert len(events) == 1
    event = events[0]
    assert event.manager_id == manager_id
    assert event.source_key == session_id
    assert event.title == "Session deleted: My working session"
    payload = json.loads(event.payload)
    assert payload["session_id"] == session_id
    assert payload["session_title"] == "My working session"
    # Deletion carries no turn status.
    assert "status" not in payload


def test_emit_session_deleted_skips_deleted_workers(
    db_uri: str, adoption_ctx: SessionAdoptionContext
) -> None:
    session_id = _uid("session-b")
    _seed_bound_session(db_uri, adoption_ctx, session_id=session_id)
    workers = adoption_ctx.worker_store.list_workers_by_target_id(session_id)
    for worker in workers:
        adoption_ctx.worker_store.update_worker(worker.id, state="deleted")

    emit_session_deleted_event(session_id=session_id)

    assert adoption_ctx.task_event_store.list_events(event_type=SESSION_DELETED_EVENT_TYPE) == []


def test_emit_session_deleted_skips_archived_task(
    db_uri: str, adoption_ctx: SessionAdoptionContext
) -> None:
    session_id = _uid("session-c")
    _seed_bound_session(db_uri, adoption_ctx, session_id=session_id, task_state="archived")

    emit_session_deleted_event(session_id=session_id)

    assert adoption_ctx.task_event_store.list_events(event_type=SESSION_DELETED_EVENT_TYPE) == []


def test_emit_session_deleted_dedupes_pending_per_manager(
    db_uri: str, adoption_ctx: SessionAdoptionContext
) -> None:
    session_id = _uid("session-d")
    _seed_bound_session(db_uri, adoption_ctx, session_id=session_id)

    emit_session_deleted_event(session_id=session_id)
    emit_session_deleted_event(session_id=session_id)

    events = adoption_ctx.task_event_store.list_events(
        event_type=SESSION_DELETED_EVENT_TYPE, state="routed"
    )
    assert len(events) == 1


def test_emit_session_deleted_not_suppressed_by_pending_turn_finished(
    db_uri: str, adoption_ctx: SessionAdoptionContext
) -> None:
    """A pending turn-finished signal must not swallow the deletion signal."""
    session_id = _uid("session-e")
    manager_id = _seed_bound_session(db_uri, adoption_ctx, session_id=session_id)
    adoption_ctx.task_event_store.create_event(
        uuid.uuid4().hex,
        SESSION_TURN_FINISHED_EVENT_TYPE,
        "Session turn finished: x",
        manager_id=manager_id,
        source_key=session_id,
        state="routed",
        owner_user_id="alice",
    )

    emit_session_deleted_event(session_id=session_id)

    deleted = adoption_ctx.task_event_store.list_events(
        event_type=SESSION_DELETED_EVENT_TYPE, state="routed"
    )
    assert len(deleted) == 1
    assert deleted[0].manager_id == manager_id


def test_emit_unbound_only_signals_when_turn_finished_in_flight(
    db_uri: str, adoption_ctx: SessionAdoptionContext
) -> None:
    """Unbound sessions are untracked — no in-flight turn, no deletion signal."""
    session_id = _uid("session-f0")
    conv = adoption_ctx.conversation_store.create_conversation(
        title="Orphan session", agent_id=_uid("agent-y")
    )

    emit_session_deleted_event_for_pending_turn(
        session_id=session_id, conv=conv, owner_user_id="bob"
    )
    assert adoption_ctx.task_event_store.list_events(event_type=SESSION_DELETED_EVENT_TYPE) == []

    # A pending turn-finished makes the deletion signal necessary.
    adoption_ctx.task_event_store.create_event(
        uuid.uuid4().hex,
        SESSION_TURN_FINISHED_EVENT_TYPE,
        "Session turn finished: x",
        source_key=session_id,
        state="awaiting_grouping",
        owner_user_id="bob",
    )
    emit_session_deleted_event_for_pending_turn(
        session_id=session_id, conv=conv, owner_user_id="bob"
    )

    events = adoption_ctx.task_event_store.list_events(
        event_type=SESSION_DELETED_EVENT_TYPE, state="awaiting_grouping"
    )
    assert len(events) == 1
    assert events[0].manager_id is None
    assert events[0].owner_user_id == "bob"
    assert events[0].source_key == session_id


def test_emit_unbound_follows_routed_turn_finished_to_its_manager(
    db_uri: str, adoption_ctx: SessionAdoptionContext
) -> None:
    """A manager already holding a routed turn-finished gets the deletion too."""
    session_id = _uid("session-f1")
    conv = adoption_ctx.conversation_store.create_conversation(
        title="Orphan session", agent_id=_uid("agent-y")
    )
    manager_id = _uid("mgr-holder")
    adoption_ctx.manager_store.upsert(
        manager_id,
        owner_user_id="carol",
        role_key="manager:default",
        description="holding the turn signal",
        conversation_id=_uid("mgr-holder-conv"),
    )
    adoption_ctx.task_event_store.create_event(
        uuid.uuid4().hex,
        SESSION_TURN_FINISHED_EVENT_TYPE,
        "Session turn finished: x",
        manager_id=manager_id,
        source_key=session_id,
        state="routed",
        owner_user_id="carol",
    )

    emit_session_deleted_event_for_pending_turn(
        session_id=session_id, conv=conv, owner_user_id="bob"
    )

    deleted = adoption_ctx.task_event_store.list_events(
        event_type=SESSION_DELETED_EVENT_TYPE, state="routed"
    )
    assert len(deleted) == 1
    assert deleted[0].manager_id == manager_id
    # Attributed to the manager's own owner so it lands in the same manager
    # queue (and batch) as the turn-finished it supersedes.
    assert deleted[0].owner_user_id == "carol"
    # The turn was only routed — nothing is awaiting the broker.
    assert (
        adoption_ctx.task_event_store.list_events(
            event_type=SESSION_DELETED_EVENT_TYPE, state="awaiting_grouping"
        )
        == []
    )


def test_emit_unbound_dedupes_pending_per_destination(
    db_uri: str, adoption_ctx: SessionAdoptionContext
) -> None:
    session_id = _uid("session-f2")
    conv = adoption_ctx.conversation_store.create_conversation(
        title="Orphan session", agent_id=_uid("agent-y")
    )
    adoption_ctx.task_event_store.create_event(
        uuid.uuid4().hex,
        SESSION_TURN_FINISHED_EVENT_TYPE,
        "Session turn finished: x",
        source_key=session_id,
        state="awaiting_grouping",
        owner_user_id="bob",
    )

    emit_session_deleted_event_for_pending_turn(
        session_id=session_id, conv=conv, owner_user_id="bob"
    )
    emit_session_deleted_event_for_pending_turn(
        session_id=session_id, conv=conv, owner_user_id="bob"
    )

    events = adoption_ctx.task_event_store.list_events(
        event_type=SESSION_DELETED_EVENT_TYPE, state="awaiting_grouping"
    )
    assert len(events) == 1


def test_emit_noop_without_adoption_context(db_uri: str) -> None:
    configure_session_adoption(None)
    emit_session_deleted_event(session_id="conv-none")
    store = SqlAlchemyTaskEventStore(db_uri)
    assert store.list_events(event_type=SESSION_DELETED_EVENT_TYPE) == []


# ── Delete-endpoint helper gating ──────────────────────────────────


@pytest.fixture()
def watcher_enabled(monkeypatch: pytest.MonkeyPatch):
    """Force the session-watcher gate open for helper-level tests."""
    from omnigent.server.routes._sessions import helpers as sessions_helpers

    monkeypatch.setattr(sessions_helpers, "_session_watcher_enabled", lambda: True)
    return sessions_helpers


def test_helper_skips_manager_conversation(
    db_uri: str, adoption_ctx: SessionAdoptionContext, watcher_enabled
) -> None:
    """A manager role session's deletion carries no adoption signal."""
    from omnigent.server.routes._sessions.helpers import maybe_emit_session_deleted_event

    session_id = _uid("session-mgr")
    adoption_ctx.manager_store.upsert(
        _uid("mgr-self"),
        owner_user_id="alice",
        role_key="manager:default",
        description="manager whose conversation is the session",
        conversation_id=session_id,
    )

    maybe_emit_session_deleted_event(session_id, _pre_delete_conv(session_id))

    assert adoption_ctx.task_event_store.list_events(event_type=SESSION_DELETED_EVENT_TYPE) == []


def test_helper_skips_sub_agent_and_role_labeled_sessions(
    db_uri: str, adoption_ctx: SessionAdoptionContext, watcher_enabled
) -> None:
    """Sub-agent and system-role (broker/secretary) deletions emit nothing."""
    from omnigent.agent_tasks.session_labels import ROLE_LABEL
    from omnigent.entities.conversation import Conversation
    from omnigent.server.routes._sessions.helpers import maybe_emit_session_deleted_event

    sub = Conversation(
        id=_uid("session-sub"),
        created_at=0,
        updated_at=0,
        root_conversation_id=_uid("session-sub"),
        kind="sub_agent",
    )
    maybe_emit_session_deleted_event(sub.id, sub)

    labeled = Conversation(
        id=_uid("session-role"),
        created_at=0,
        updated_at=0,
        root_conversation_id=_uid("session-role"),
        labels={ROLE_LABEL: "broker"},
    )
    maybe_emit_session_deleted_event(labeled.id, labeled)

    assert adoption_ctx.task_event_store.list_events(event_type=SESSION_DELETED_EVENT_TYPE) == []


def test_helper_unbound_only_signals_when_turn_in_flight(
    db_uri: str, adoption_ctx: SessionAdoptionContext, watcher_enabled
) -> None:
    """An unbound session's deletion emits only with a turn-finished in flight."""
    from omnigent.server.routes._sessions.helpers import maybe_emit_session_deleted_event

    session_id = _uid("session-unbound")
    conv = adoption_ctx.conversation_store.create_conversation(
        title="Plain session", agent_id=_uid("agent-z")
    )

    maybe_emit_session_deleted_event(session_id, conv)
    assert adoption_ctx.task_event_store.list_events(event_type=SESSION_DELETED_EVENT_TYPE) == []

    adoption_ctx.task_event_store.create_event(
        uuid.uuid4().hex,
        SESSION_TURN_FINISHED_EVENT_TYPE,
        "Session turn finished: Plain session",
        source_key=session_id,
        state="awaiting_grouping",
        owner_user_id="__anonymous__",
    )
    maybe_emit_session_deleted_event(session_id, conv)

    events = adoption_ctx.task_event_store.list_events(
        event_type=SESSION_DELETED_EVENT_TYPE, state="awaiting_grouping"
    )
    assert len(events) == 1
    assert events[0].source_key == session_id


# ── Manager notice rendering ────────────────────────────────────────


def _deleted_event(
    *,
    source_key: str = "conv-del",
    payload: dict | None = None,
) -> object:
    return type(
        "E",
        (),
        {
            "event_type": SESSION_DELETED_EVENT_TYPE,
            "title": "Session deleted: My session",
            "source_key": source_key,
            "payload": json.dumps(
                payload
                or {
                    "session_id": "conv-del",
                    "session_title": "My session",
                    "project_name": "Atlas",
                }
            ),
        },
    )()


def test_notice_single_deleted_event() -> None:
    notice = _format_manager_notice([_deleted_event()])
    assert "session.deleted" in notice
    assert "'My session' was deleted" in notice
    assert "Project: Atlas" in notice
    assert "conv-del" in notice
    assert "Reconcile" in notice


def test_notice_batch_deleted_supersedes_turn_finished() -> None:
    """A pending turn-finished + deletion batch reads as deletion, not turns."""
    finished = type(
        "E",
        (),
        {
            "event_type": SESSION_TURN_FINISHED_EVENT_TYPE,
            "title": "Session turn finished: My session",
            "source_key": "conv-del",
            "payload": json.dumps({"session_id": "conv-del", "session_title": "My session"}),
        },
    )()
    notice = _format_session_batch_notice([finished, _deleted_event()])
    assert "was deleted" in notice
    assert "finished" not in notice.split("was deleted")[0]


def test_notice_batch_only_turn_finished_unchanged() -> None:
    """Without a deletion, the turn-finished batch rendering is untouched."""
    finished = type(
        "E",
        (),
        {
            "event_type": SESSION_TURN_FINISHED_EVENT_TYPE,
            "title": "Session turn finished: My session",
            "source_key": "conv-del",
            "payload": json.dumps({"session_id": "conv-del", "session_title": "My session"}),
        },
    )()
    notice = _format_session_batch_notice([finished, finished])
    assert "finished 2 turns" in notice
    assert "was deleted" not in notice


def test_adoption_module_exports_deleted_emitters() -> None:
    """The emit pair is importable next to the turn-finished pair."""
    assert hasattr(adoption, "emit_session_deleted_event")
    assert hasattr(adoption, "emit_session_deleted_event_for_pending_turn")
