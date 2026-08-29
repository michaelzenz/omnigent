"""Orphan session adoption — auto-adopt and broker fallback."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.event_types import SESSION_ORPHAN_EVENT_TYPE, SESSION_TURN_FINISHED_EVENT_TYPE
from omnigent.agent_tasks.items import create_task_item
from omnigent.agent_tasks.routing import route_event_to_task
from omnigent.agent_tasks.scoring import rank_tasks_for_event_tags
from omnigent.agent_tasks.session_labels import ADOPTION_DISMISSED_LABEL
from omnigent.agent_tasks.session_profile import resolve_session_routing_tags
from omnigent.agent_tasks.session_task import task_for_session
from omnigent.agent_tasks.task_match import live_tasks
from omnigent.agent_tasks.workers import _generate_worker_id
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, Worker
from omnigent.entities.conversation import Conversation
from omnigent.entities.agent_queue import AgentQueueKey
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.agent_task.tags import tags_to_payload
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WORKER_KIND_EXTERNAL, WorkerStore

_logger = logging.getLogger(__name__)

SESSION_ADOPTED = "session.adopted"

# Orphan adoption is active: sessions that finish a turn with no existing
# worker binding enter the adoption pipeline.
_ORPHAN_SESSION_ADOPTION_ENABLED = True

# Score threshold for confident auto-adopt. At or above this, the system
# creates the Worker + human_action item directly. Below it, the session.orphan
# event falls to the broker for manual triage.
_AUTO_ADOPT_SCORE_THRESHOLD = 0.5


def orphan_session_adoption_enabled() -> bool:
    """Return whether turn-finish triggers session adoption."""
    return _ORPHAN_SESSION_ADOPTION_ENABLED


@dataclass
class SessionAdoptionContext:
    """Stores required to handle orphan session adoption."""

    task_store: TaskStore
    task_event_store: TaskEventStore
    worker_store: WorkerStore
    conversation_store: ConversationStore
    task_item_store: TaskItemStore
    task_role_profile_store: TaskRoleProfileStore | None = None
    host_store: HostStore | None = None
    runner_router: RunnerRouter | None = None
    agent_queue_store: AgentQueueStore | None = None


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
        if host is not None and host.user_id:
            return host.user_id
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
    source: str = "internal",
) -> bool:
    """Trigger adoption for a session that just finished a turn.

    If the best-matching task scores above the threshold, creates a Worker
    and a human_action item directly (auto-adopt). Otherwise, enqueues a
    ``session.orphan`` event for the broker to triage.
    """
    if not _ORPHAN_SESSION_ADOPTION_ENABLED:
        return False
    if _context is None:
        return False
    owner_user_id = resolve_owner_user_id(
        user_id=user_id,
        host_id=host_id,
        host_store=_context.host_store,
    )
    conv = _context.conversation_store.get_conversation(session_id)
    if not is_orphan_candidate(
        conv,
        task_store=_context.task_store,
        worker_store=_context.worker_store,
    ):
        return False
    assert conv is not None

    active_tasks = live_tasks(_context.task_store)
    if not active_tasks:
        # No live tasks — broker creates one during triage.
        return await enqueue_orphan_session(session_id, owner_user_id=owner_user_id)

    routing_tags = resolve_session_routing_tags(session_id, conv)
    ranked = rank_tasks_for_event_tags(
        event_tags=routing_tags,
        tasks=active_tasks,
        task_store=_context.task_store,
    )

    if ranked and ranked[0][1] >= _AUTO_ADOPT_SCORE_THRESHOLD:
        # Confident match — auto-adopt directly.
        task, score = ranked[0]
        adopt_session_to_task(
            session_id=session_id,
            task=task,
            conv=conv,
            score=score,
            owner_user_id=owner_user_id,
        )
        return True

    # Low score or no match — fall back to broker triage.
    return await enqueue_orphan_session(session_id, owner_user_id=owner_user_id)


def adopt_session_to_task(
    *,
    session_id: str,
    task: Task,
    conv: Conversation,
    score: float = 0.0,
    owner_user_id: str | None = None,
) -> tuple[str, str]:
    """Create a Worker and a human_action item for an adopted session.

    :returns: (worker_id, item_id)
    """
    assert _context is not None
    worker_id = _generate_worker_id()
    _context.worker_store.create_worker(
        worker_id,
        task.id,
        kind=WORKER_KIND_EXTERNAL,
        target_id=session_id,
        state="idle",
        provider_name=conv.title or session_id,
    )
    item = create_task_item(
        task=task,
        task_item_store=_context.task_item_store,
        worker_store=_context.worker_store,
        title=f'New session "{conv.title or session_id}" related to this task — can you confirm?',
        description=f"Session: {session_id}\nMatch score: {score:.2f}",
        kind="human_action",
        state="pending",
        created_by="broker",
        internal_note=json.dumps({"worker_id": worker_id, "session_id": session_id}),
    )
    return worker_id, item.id


async def enqueue_orphan_session(
    session_id: str,
    *,
    owner_user_id: str,
) -> bool:
    """Record an orphan session for broker triage.

    Creates a ``session.orphan`` task event in ``awaiting_grouping`` state.
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

    # Fetch the last user message and last assistant response so the
    # broker can triage without calling sys_session_get_history.
    last_user_message = None
    last_agent_response = None
    try:
        items = _context.conversation_store.list_items(session_id, limit=50, order="desc")
        for item in reversed(items.data):
            if item.type != "message":
                continue
            data = item.data
            if not hasattr(data, "role"):
                continue
            text_parts = [
                block.get("text", "")
                for block in (data.content or [])
                if isinstance(block, dict) and block.get("type") in ("input_text", "output_text", "text")
            ]
            text = " ".join(text_parts).strip()
            if not text:
                continue
            if data.role == "user" and last_user_message is None:
                last_user_message = text[:2000]
            elif data.role == "assistant" and last_agent_response is None:
                last_agent_response = text[:2000]
            if last_user_message is not None and last_agent_response is not None:
                break
    except Exception:
        pass

    title = f"Adopt session: {conv.title or session_id}"
    payload = json.dumps({
        "session_id": session_id,
        "session_title": conv.title,
        "host_id": conv.host_id,
        "workspace": conv.workspace,
        "last_user_message": last_user_message,
        "last_agent_response": last_agent_response,
    })
    _context.task_event_store.create_event(
        uuid.uuid4().hex,
        SESSION_ORPHAN_EVENT_TYPE,
        title,
        source="adoption",
        source_key=session_id,
        state="awaiting_grouping",
        owner_user_id=owner_user_id,
        payload=payload,
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


# ── External session adoption (watcher-discovered sessions) ────────


def find_open_external_adoption_proposal(
    task_event_store: TaskEventStore,
    session_hint: str,
) -> TaskEvent | None:
    """Return the open adoption proposal for an external session hint."""
    for event in task_event_store.list_events(
        state="received",
        event_type="session.adoption",
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
    """Create a user-gated adoption proposal for a watcher-discovered session."""
    if task_id is not None:
        task = task_store.get(task_id)
        if task is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
    else:
        task_id = uuid.uuid4().hex
        task_store.create(
            task_id,
            f"External session: {session_hint}",
            f"Adopt external session {session_hint} into a managed task",
        )
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
        "session.adoption",
        f"Adopt external session: {session_hint}",
        source_key=session_hint,
        source="broker",
        payload=json.dumps(payload),
        task_id=task.id,
        state="received",
        owner_user_id=owner_user_id,
    )
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
    params: Any | None = None,
    proposal_event: TaskEvent | None = None,
    session_creator: Any | None = None,
    app_state: Any | None = None,
    user_id: str | None = None,
) -> tuple[TaskEvent, TaskEvent]:
    """Bind a watcher-discovered external session to a task."""
    task = task_store.get(task_id)
    if task is None:
        raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)

    worker_store.create_worker(
        _generate_worker_id(),
        task.id,
        kind=WORKER_KIND_EXTERNAL,
        target_id=session_hint,
        state="idle",
        provider_name="External session",
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
    """Dismiss an external session adoption proposal."""
    del session_hint
    if proposal_event is None:
        return None
    return task_event_store.update_event(proposal_event.id, state="dismissed")


# ── Turn-finish event for adopted internal sessions ────────────────


def emit_turn_finished_event(
    *,
    session_id: str,
    worker: Worker,
    status: str = "idle",
) -> None:
    """Emit a ``session.turn.finished`` event and directly enqueue a
    standalone manager notice.

    Bypasses packager batching — each turn is its own notice so the manager
    handles them independently. The event is born ``routed``, immediately
    enqueued onto the manager queue, then marked ``reconciled`` so the
    packager never picks it up.
    """
    if _context is None:
        return
    task = _context.task_store.get(worker.task_id)
    if task is None:
        return
    conv = _context.conversation_store.get_conversation(session_id)
    session_title = conv.title if conv is not None else session_id
    owner = task.owner_user_id or "__anonymous__"
    payload = json.dumps({
        "session_id": session_id,
        "session_title": session_title,
        "worker_id": worker.id,
        "status": status,
    })
    title = f"Session turn finished: {session_title}"
    try:
        event = _context.task_event_store.create_event(
            uuid.uuid4().hex,
            SESSION_TURN_FINISHED_EVENT_TYPE,
            title,
            task_id=task.id,
            source="adoption",
            source_key=session_id,
            state="routed",
            payload=payload,
            owner_user_id=owner,
        )
        _context.task_event_store.update_event(event.id, routed_at=now_epoch())
    except Exception:
        _logger.exception(
            "failed to emit %s event for session %s on task %s",
            SESSION_TURN_FINISHED_EVENT_TYPE,
            session_id,
            task.id,
        )
