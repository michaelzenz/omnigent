"""Orphan session adoption — propose, accept, and reject."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.bootstrap import BootstrapParams, resolve_bootstrap_params
from omnigent.agent_tasks.agent_builtins import TASK_SECRETARY_ROLE
from omnigent.agent_tasks.routing import route_event_to_task
from omnigent.agent_tasks.session_task import task_for_session
from omnigent.agent_tasks.workers import _generate_worker_id
from omnigent.agent_tasks.manager_agent import (
    resolve_manager_profile_id,
    resolve_manager_profile_id_for_task,
)
from omnigent.agent_tasks.scoring import rank_tasks_for_event_tags
from omnigent.agent_tasks.task_match import live_tasks
from omnigent.agent_tasks.session_labels import ADOPTION_DISMISSED_LABEL
from omnigent.agent_tasks.session_profile import resolve_session_routing_tags
from omnigent.stores.agent_task.tags import tags_to_payload
from omnigent.agent_tasks.wake import (
    wake_secretary_for_orphan_sessions,
    wake_task_manager_for_event,
)
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent
from omnigent.entities.conversation import Conversation
from omnigent.entities.task_role_profile import UserTaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WORKER_KIND_EXTERNAL, WorkerStore

_logger = logging.getLogger(__name__)

SESSION_ADOPTION_PROPOSAL = "session.adoption"
SESSION_ADOPTED = "session.adopted"

_PENDING_BY_USER: dict[str, list[str]] = {}
_DEBOUNCE_HANDLES: dict[str, Any] = {}
_DEBOUNCE_SECONDS = 2.0

# Orphan adoption on import/session-create is off until secretary UX is ready.
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
    """Map a session to the secretary owner user."""
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
    if task_for_session(
        conv.id,
        task_store=task_store,
        worker_store=worker_store,
    ) is not None:
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
    Queue an orphan session for secretary routing-profile work.

    :returns: ``True`` when the session was queued.
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
    pending = _PENDING_BY_USER.setdefault(owner_user_id, [])
    if session_id not in pending:
        pending.append(session_id)
    await _schedule_secretary_wake(owner_user_id)
    return True


async def flush_pending_orphan_sessions(owner_user_id: str) -> None:
    """Wake the secretary for any queued orphan sessions for one user."""
    if _context is None or _context.task_role_profile_store is None:
        return
    session_ids = _PENDING_BY_USER.pop(owner_user_id, [])
    if not session_ids:
        return
    await wake_secretary_for_orphan_sessions(
        user_id=owner_user_id,
        session_ids=session_ids,
        task_role_profile_store=_context.task_role_profile_store,
        conversation_store=_context.conversation_store,
        runner_router=_context.runner_router,
    )


async def _schedule_secretary_wake(owner_user_id: str) -> None:
    if _context is None:
        return
    profile = (
        _context.task_role_profile_store.get(owner_user_id, TASK_SECRETARY_ROLE)
        if _context.task_role_profile_store is not None
        else None
    )
    if profile is None or profile.conversation_id is None:
        _logger.info(
            "orphan session queued for user %s; secretary session not ready",
            owner_user_id,
        )
        return
    existing = _DEBOUNCE_HANDLES.get(owner_user_id)
    if existing is not None and not existing.done():
        return

    async def _debounced() -> None:
        import asyncio

        await asyncio.sleep(_DEBOUNCE_SECONDS)
        await flush_pending_orphan_sessions(owner_user_id)
        _DEBOUNCE_HANDLES.pop(owner_user_id, None)

    import asyncio

    _DEBOUNCE_HANDLES[owner_user_id] = asyncio.create_task(_debounced())


def _candidate_payload(
    ranked: list[tuple[Task, float]],
    *,
    agent_store: AgentStore,
    conversation_store: ConversationStore,
) -> dict[str, Any]:
    candidates = [
        {
            "task_id": task.id,
            "title": task.title,
            "score": round(score, 4),
            "agent_profile_id": resolve_manager_profile_id_for_task(
                task,
                agent_store=agent_store,
                conversation_store=conversation_store,
            ),
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
    agent_store: AgentStore,
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
    payload = _candidate_payload(
        ranked,
        agent_store=agent_store,
        conversation_store=conversation_store,
    )
    payload["session_id"] = session_id
    payload["routing_tags"] = tags_to_payload(routing_tags)
    event_id = uuid.uuid4().hex
    return task_event_store.create_event(
        event_id,
        SESSION_ADOPTION_PROPOSAL,
        f"Adopt session: {conv.title or session_id}",
        source_key=session_id,
        payload=json.dumps(payload),
        source="secretary",
        state="received",
    )


async def adopt_session(
    *,
    session_id: str,
    task_id: str,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    runner_router: RunnerRouter | None,
    params: BootstrapParams,
    proposal_event: TaskEvent | None = None,
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
        conv.agent_id,
        kind=WORKER_KIND_EXTERNAL,
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
    routed = route_event_to_task(
        event=adopted_event,
        task=task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
        params=params,
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
    routed_task = task_store.get(task.id)
    if routed_task is not None and routed_task.manager_conversation_id is not None:
        await wake_task_manager_for_event(
            manager_conversation_id=routed_task.manager_conversation_id,
            event=routed,
            conversation_store=conversation_store,
            runner_router=runner_router,
        )
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
    updated = task_event_store.update_event(proposal_event.id, state="dismissed")
    return updated


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
