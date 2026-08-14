"""Orphan session adoption — propose, accept, and reject."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.bootstrap import BootstrapParams
from omnigent.agent_tasks.event_types import SESSION_ORPHAN_EVENT_TYPE
from omnigent.agent_tasks.routing import route_event_to_task
from omnigent.agent_tasks.scoring import rank_tasks_for_event_tags
from omnigent.agent_tasks.session_labels import ADOPTION_DISMISSED_LABEL
from omnigent.agent_tasks.session_profile import resolve_session_routing_tags
from omnigent.agent_tasks.session_task import task_for_session
from omnigent.agent_tasks.task_match import live_tasks
from omnigent.agent_tasks.workers import _generate_worker_id
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent
from omnigent.entities.conversation import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_task.tags import tags_to_payload
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WORKER_KIND_EXTERNAL, WorkerStore

_logger = logging.getLogger(__name__)

SESSION_ADOPTION_PROPOSAL = "session.adoption"
SESSION_ADOPTED = "session.adopted"

# Orphan adoption on import/session-create is off until broker UX is ready.
_ORPHAN_SESSION_ADOPTION_ENABLED = False


def orphan_session_adoption_enabled() -> bool:
    """Return whether import/create hooks enqueue sessions for adoption."""
    return _ORPHAN_SESSION_ADOPTION_ENABLED


@dataclass
class SessionAdoptionContext:
    """Stores required to handle orphan session adoption."""

    task_store: TaskStore
    task_event_store: TaskEventStore
    worker_store: WorkerStore
    conversation_store: ConversationStore
    task_role_profile_store: TaskRoleProfileStore | None = None
    host_store: HostStore | None = None
    runner_router: RunnerRouter | None = None


_context: SessionAdoptionContext | None = None


def configure_session_adoption(context: SessionAdoptionContext | None) -> None:
    """Register or clear the global session-adoption handler."""
    global _context
    _context = context


def get_session_adoption_context() -> SessionAdoptionContext | None:
    """Return the configured session-adoption handler context."""
    return _context


def resolve_owner_user_id(
    *,
    user_id: str | None,
    host_id: str | None,
    host_store: HostStore | None,
) -> str:
    """Map a session to the broker owner user."""
    if host_id and host_store is not None:
        host = host_store.get_host(host_id)
        if host is not None and host.owner:
            return host.owner
    if user_id is not None:
        return user_id
    return "__anonymous__"


def is_orphan_candidate(
    conv: Conversation | None,
    *,
    task_store: TaskStore,
    worker_store: WorkerStore,
) -> bool:
    """Return whether a conversation should enter the adoption pipeline."""
    if conv is None:
        return False
    if conv.kind == "sub_agent":
        return False
    if (
        task_for_session(
            conv.id,
            task_store=task_store,
            worker_store=worker_store,
        )
        is not None
    ):
        return False
    if conv.labels.get(ADOPTION_DISMISSED_LABEL) == "1":
        return False
    return True


async def notify_new_session(
    session_id: str,
    *,
    user_id: str | None = None,
    host_id: str | None = None,
) -> bool:
    """Enqueue a newly created or imported session for adoption routing."""
    if not _ORPHAN_SESSION_ADOPTION_ENABLED:
        return False
    if _context is None:
        return False
    owner_user_id = resolve_owner_user_id(
        user_id=user_id,
        host_id=host_id,
        host_store=_context.host_store,
    )
    return await enqueue_orphan_session(session_id, owner_user_id=owner_user_id)


async def enqueue_orphan_session(
    session_id: str,
    *,
    owner_user_id: str,
) -> bool:
    """
    Record an orphan session for broker routing-profile work.

    Creates a ``session.orphan`` task event in ``awaiting_grouping`` state,
    attributed to the owner. The broker packager polls it like any other
    stalled event, so this is durable across restarts and needs no in-memory
    queue or direct wake.

    :returns: ``True`` when a new orphan event was created.
    """
    if _context is None:
        return False
    conv = _context.conversation_store.get_conversation(session_id)
    if not is_orphan_candidate(
        conv,
        task_store=_context.task_store,
        worker_store=_context.worker_store,
    ):
        return False
    assert conv is not None
    if find_open_orphan_event(_context.task_event_store, session_id) is not None:
        return False
    title = f"Adopt session: {conv.title or session_id}"
    _context.task_event_store.create_event(
        uuid.uuid4().hex,
        SESSION_ORPHAN_EVENT_TYPE,
        title,
        source="adoption",
        source_key=session_id,
        state="awaiting_grouping",
        owner_user_id=owner_user_id,
    )
    return True


def find_open_orphan_event(
    task_event_store: TaskEventStore,
    session_id: str,
) -> TaskEvent | None:
    """Return the open ``session.orphan`` event for a session, if any."""
    for event in task_event_store.list_events(
        state="awaiting_grouping",
        event_type=SESSION_ORPHAN_EVENT_TYPE,
    ):
        if event.source_key == session_id:
            return event
    return None


def _candidate_payload(ranked: list[tuple[Task, float]]) -> dict[str, Any]:
    candidates = [
        {
            "task_id": task.id,
            "title": task.title,
            "score": round(score, 4),
            "manager_role_key": task.manager_role_key,
        }
        for task, score in ranked
    ]
    recommended_task_id = candidates[0]["task_id"] if candidates else None
    return {
        "candidates": candidates,
        "recommended_task_id": recommended_task_id,
    }


def propose_session_adoption(
    *,
    session_id: str,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    owner_user_id: str | None = None,
) -> TaskEvent:
    """Score tasks and create a user-gated session adoption proposal."""
    conv = conversation_store.get_conversation(session_id)
    if not is_orphan_candidate(
        conv,
        task_store=task_store,
        worker_store=worker_store,
    ):
        raise OmnigentError(
            "Session is not eligible for adoption",
            code=ErrorCode.CONFLICT,
        )
    assert conv is not None
    if not resolve_session_routing_tags(session_id, conv):
        raise OmnigentError(
            "routing tags are required before proposing adoption",
            code=ErrorCode.INVALID_INPUT,
        )
    active_tasks = [
        task
        for task in live_tasks(task_store)
        if owner_user_id is None
        or task.owner_user_id is None
        or task.owner_user_id == owner_user_id
    ]
    routing_tags = resolve_session_routing_tags(session_id, conv)
    ranked = rank_tasks_for_event_tags(
        event_tags=routing_tags,
        tasks=active_tasks,
        task_store=task_store,
    )
    payload = _candidate_payload(ranked)
    payload["session_id"] = session_id
    payload["routing_tags"] = tags_to_payload(routing_tags)
    event_id = uuid.uuid4().hex
    proposal = task_event_store.create_event(
        event_id,
        SESSION_ADOPTION_PROPOSAL,
        f"Adopt session: {conv.title or session_id}",
        source_key=session_id,
        payload=json.dumps(payload),
        source="broker",
        state="received",
    )
    # The broker has produced a proposal, so the orphan trigger event is done.
    orphan = find_open_orphan_event(task_event_store, session_id)
    if orphan is not None:
        task_event_store.update_event(
            orphan.id,
            state="reconciled",
            processed_at=now_epoch(),
        )
    return proposal


async def adopt_session(
    *,
    session_id: str,
    task_id: str,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    params: BootstrapParams,
    proposal_event: TaskEvent | None = None,
    session_creator: Any | None = None,
    app_state: Any | None = None,
    user_id: str | None = None,
) -> tuple[TaskEvent, TaskEvent]:
    """
    Bind an orphan session to a task and wake its manager.

    :returns: The processed adoption proposal (if any) and manager triage event.
    """
    conv = conversation_store.get_conversation(session_id)
    if not is_orphan_candidate(
        conv,
        task_store=task_store,
        worker_store=worker_store,
    ):
        raise OmnigentError(
            "Session is not eligible for adoption",
            code=ErrorCode.CONFLICT,
        )
    task = task_store.get(task_id)
    if task is None:
        raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)

    assert conv is not None
    worker_store.create_worker(
        _generate_worker_id(),
        task.id,
        kind=WORKER_KIND_EXTERNAL,
        agent_profile_id=conv.agent_id,
        session_id=session_id,
    )
    adopted_event_id = uuid.uuid4().hex
    adopted_event = task_event_store.create_event(
        adopted_event_id,
        SESSION_ADOPTED,
        f"Session adopted: {conv.title if conv is not None else session_id}",
        source_key=session_id,
        source="adoption",
        state="received",
    )
    routed = await route_event_to_task(
        event=adopted_event,
        task=task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        params=params,
        session_creator=session_creator,
        app_state=app_state,
        user_id=user_id,
    )
    processed_proposal = proposal_event
    if proposal_event is not None:
        updated = task_event_store.update_event(
            proposal_event.id,
            state="reconciled",
            processed_at=now_epoch(),
            task_id=task.id,
        )
        processed_proposal = updated if updated is not None else proposal_event
    _ = task_store.get(task.id)  # the event is now routed; the manager packager picks it up.
    return processed_proposal or routed, routed


def reject_session_adoption(
    *,
    session_id: str,
    conversation_store: ConversationStore,
    task_event_store: TaskEventStore,
    proposal_event: TaskEvent | None = None,
) -> TaskEvent | None:
    """Mark a session as intentionally unadopted."""
    conv = conversation_store.get_conversation(session_id)
    if conv is None:
        raise OmnigentError("Session not found", code=ErrorCode.NOT_FOUND)
    labels = dict(conv.labels)
    labels[ADOPTION_DISMISSED_LABEL] = "1"
    conversation_store.set_labels(session_id, labels)
    if proposal_event is None:
        return None
    return task_event_store.update_event(proposal_event.id, state="dismissed")


def find_open_adoption_proposal(
    task_event_store: TaskEventStore,
    session_id: str,
) -> TaskEvent | None:
    """Return the open adoption proposal for a session, if any."""
    for event in task_event_store.list_events(
        state="received",
        event_type=SESSION_ADOPTION_PROPOSAL,
    ):
        if event.source_key == session_id:
            return event
    return None


# ── External session adoption (watcher-discovered sessions) ────────


def find_open_external_adoption_proposal(
    task_event_store: TaskEventStore,
    session_hint: str,
) -> TaskEvent | None:
    """Return the open adoption proposal for an external session hint."""
    for event in task_event_store.list_events(
        state="received",
        event_type=SESSION_ADOPTION_PROPOSAL,
    ):
        if event.source_key == session_hint:
            return event
    return None


def propose_external_session_adoption(
    *,
    session_hint: str,
    task_id: str | None,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    owner_user_id: str | None = None,
    transcript_snippet: str | None = None,
    routing_tags: list | None = None,
) -> tuple[Task, TaskEvent]:
    """Create a user-gated adoption proposal for a watcher-discovered session.

    If ``task_id`` is ``None`` the broker creates a new pending task so the
    proposal has somewhere to land. The proposal event carries the
    ``session_hint`` in its payload so the accept flow can wire the worker.
    """
    if task_id is not None:
        task = task_store.get(task_id)
        if task is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
    else:
        task_id = uuid.uuid4().hex
        task_store.create(task_id, f"External session: {session_hint}")
        task = task_store.get(task_id)
        assert task is not None

    payload: dict[str, Any] = {
        "session_hint": session_hint,
        "external": True,
    }
    if transcript_snippet:
        payload["transcript_snippet"] = transcript_snippet
    if routing_tags:
        payload["routing_tags"] = tags_to_payload(routing_tags)

    event_id = uuid.uuid4().hex
    proposal = task_event_store.create_event(
        event_id,
        SESSION_ADOPTION_PROPOSAL,
        f"Adopt external session: {session_hint}",
        source_key=session_hint,
        source="broker",
        payload=json.dumps(payload),
        task_id=task.id,
        state="received",
        owner_user_id=owner_user_id,
    )
    # Mark the discovered event as reconciled — the broker has triaged it.
    from omnigent.agent_tasks.event_types import EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE

    for disc in task_event_store.list_events(
        state="awaiting_grouping",
        event_type=EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
    ):
        if disc.source_key == session_hint:
            task_event_store.update_event(
                disc.id,
                state="reconciled",
                processed_at=now_epoch(),
            )
            break
    return task, proposal


async def adopt_external_session(
    *,
    session_hint: str,
    task_id: str,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    params: BootstrapParams,
    proposal_event: TaskEvent | None = None,
    session_creator: Any | None = None,
    app_state: Any | None = None,
    user_id: str | None = None,
) -> tuple[TaskEvent, TaskEvent]:
    """Bind a watcher-discovered external session to a task.

    Creates a ``WORKER_KIND_EXTERNAL`` worker with ``external_session_hint``
    so future ``external.session.updated`` events auto-route to this task.
    """
    task = task_store.get(task_id)
    if task is None:
        raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)

    worker_store.create_worker(
        _generate_worker_id(),
        task.id,
        kind=WORKER_KIND_EXTERNAL,
        agent_profile_id=None,
        session_id=None,
        external_session_hint=session_hint,
    )
    adopted_event = task_event_store.create_event(
        uuid.uuid4().hex,
        SESSION_ADOPTED,
        f"External session adopted: {session_hint}",
        source_key=session_hint,
        source="adoption",
        state="received",
        task_id=task.id,
    )
    routed = await route_event_to_task(
        event=adopted_event,
        task=task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        params=params,
        session_creator=session_creator,
        app_state=app_state,
        user_id=user_id,
    )
    processed_proposal = proposal_event
    if proposal_event is not None:
        updated = task_event_store.update_event(
            proposal_event.id,
            state="reconciled",
            processed_at=now_epoch(),
            task_id=task.id,
        )
        processed_proposal = updated if updated is not None else proposal_event
    return processed_proposal or routed, routed


def reject_external_session_adoption(
    *,
    session_hint: str,
    task_event_store: TaskEventStore,
    proposal_event: TaskEvent | None = None,
) -> TaskEvent | None:
    """Dismiss an external session adoption proposal.

    The dismissal is recorded so the session-watcher update endpoint can
    return ``track: false`` for this hint.
    """
    if proposal_event is None:
        return None
    return task_event_store.update_event(proposal_event.id, state="dismissed")
