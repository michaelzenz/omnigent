"""Orphan session adoption — auto-adopt and broker fallback."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.event_host import host_tag
from omnigent.agent_tasks.event_types import (
    SESSION_DELETED_EVENT_TYPE,
    SESSION_TURN_FINISHED_EVENT_TYPE,
)
from omnigent.agent_tasks.manager_discovery import _LIVE_TASK_STATES
from omnigent.agent_tasks.routing import route_event_to_task
from omnigent.agent_tasks.workers import _generate_worker_id
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, Worker
from omnigent.entities.conversation import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.agent_task.tags import tags_to_payload
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.manager_store import ManagerStore
from omnigent.stores.project_store import ProjectStore
from omnigent.stores.task_asset_store import TaskAssetStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import (
    INACTIVE_WORKER_STATES,
    WORKER_KIND_EXTERNAL,
    WORKER_KIND_INTERNAL,
    WorkerStore,
)

_logger = logging.getLogger(__name__)

SESSION_ADOPTED = "session.adopted"


def _project_name(
    project_store: ProjectStore | None,
    project_id: str | None,
    owner_user_id: str | None,
) -> str | None:
    """Resolve a session's project name, or None when unprojected/unresolvable."""
    if project_store is None or not project_id:
        return None
    try:
        project = project_store.get(project_id, user_id=owner_user_id)
    except Exception:
        _logger.exception("failed to resolve project %s", project_id)
        return None
    return project.name if project is not None else None


# Orphan adoption is active: sessions that finish a turn with no existing
@dataclass
class SessionAdoptionContext:
    """Stores required to handle orphan session adoption."""

    task_store: TaskStore
    task_event_store: TaskEventStore
    worker_store: WorkerStore
    conversation_store: ConversationStore
    task_item_store: TaskItemStore
    manager_store: ManagerStore | None = None
    task_role_profile_store: TaskRoleProfileStore | None = None
    host_store: HostStore | None = None
    runner_router: RunnerRouter | None = None
    agent_queue_store: AgentQueueStore | None = None
    project_store: ProjectStore | None = None
    task_asset_store: TaskAssetStore | None = None


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


def _workspace_asset_title(workspace: str) -> str:
    """Card label for a workspace asset: git branch name, else folder name."""
    import contextlib
    import os
    import subprocess

    folder = os.path.basename(os.path.normpath(workspace)) or workspace
    with contextlib.suppress(Exception):
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=workspace,
            capture_output=True,
            timeout=5,
            check=True,
        )
        branch = proc.stdout.decode().strip()
        if branch:
            return branch
    return folder


def _ensure_workspace_asset(
    task_id: str,
    workspace: str,
    source_worker_id: str | None = None,
) -> None:
    """Attach a deduped ``kind=workspace`` asset for an adopted session's cwd.

    Idempotent: a re-adoption (or adoption of an already-bound session) finds
    the existing row and does nothing. Best-effort — asset attachment must
    never fail the adoption itself.
    """
    import contextlib

    if _context is None or _context.task_asset_store is None:
        return
    title = _workspace_asset_title(workspace)
    with contextlib.suppress(Exception):
        # One upsert: inserts on first adoption, relabels in place when the
        # branch renamed (the URL is the asset's identity).
        _context.task_asset_store.upsert_asset(
            task_id,
            kind="workspace",
            category="workspace",
            title=title,
            url=workspace,
            source_worker_id=source_worker_id,
        )


def adopt_session_to_task(
    *,
    session_id: str,
    task: Task,
    conv: Conversation,
    title: str | None = None,
    score: float = 0.0,
    owner_user_id: str | None = None,
) -> tuple[Worker, bool]:
    """Bind a session to a task — find-or-create the (task, session) worker.
    :returns: (worker, created)
    """
    del score, owner_user_id
    assert _context is not None
    existing = _context.worker_store.find_worker_by_target_task(task.id, session_id)
    if existing is not None:
        updates: dict[str, Any] = {}
        if existing.state in INACTIVE_WORKER_STATES:
            updates.update(state="idle", needs_response=False, failure_reason=None)
        if title is not None and title != existing.title:
            updates["title"] = title
        worker = existing
        if updates:
            updated = _context.worker_store.update_worker(existing.id, **updates)
            if updated is not None:
                worker = updated
        if conv.workspace:
            _ensure_workspace_asset(task.id, conv.workspace, source_worker_id=existing.id)
        return worker, False
    worker_id = _generate_worker_id()
    worker = _context.worker_store.create_worker(
        worker_id,
        task.id,
        # The session lives in this server's conversation store — an adopted
        # *internal* lane (chat-able, server-visible status), not an
        # external-harness session.
        kind=WORKER_KIND_INTERNAL,
        target_id=session_id,
        state="idle",
        title=title,
        provider_name=conv.title or session_id,
    )
    if conv.workspace:
        _ensure_workspace_asset(task.id, conv.workspace, source_worker_id=worker_id)
    return worker, True


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
        payload=json.dumps(payload, ensure_ascii=False),
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
    proposal_event: TaskEvent | None = None,
    session_creator: Any | None = None,
    app_state: Any | None = None,
    user_id: str | None = None,
) -> tuple[TaskEvent | None, TaskEvent | None, Worker]:
    """Bind a watcher-discovered external session to a task.
    :returns: (processed proposal event, adopted event, worker)
    """
    task = task_store.get(task_id)
    if task is None:
        raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)

    existing = worker_store.find_worker_by_target_task(task.id, session_hint)
    if existing is not None:
        updates: dict[str, Any] = {}
        if existing.state in INACTIVE_WORKER_STATES:
            updates.update(state="idle", needs_response=False, failure_reason=None)
        worker = existing
        if updates:
            updated = worker_store.update_worker(existing.id, **updates)
            if updated is not None:
                worker = updated
        processed_proposal = proposal_event
        if proposal_event is not None:
            updated = task_event_store.update_event(
                proposal_event.id,
                state="reconciled",
                processed_at=now_epoch(),
                task_id=task.id,
            )
            processed_proposal = updated if updated is not None else proposal_event
        return processed_proposal, None, worker

    external_worker_id = _generate_worker_id()
    worker = worker_store.create_worker(
        external_worker_id,
        task.id,
        kind=WORKER_KIND_EXTERNAL,
        target_id=session_hint,
        state="idle",
        provider_name="External session",
    )
    # External (harness) sessions have no local conversation row to read a
    # workspace from — their watchers report updates without one.
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
    return processed_proposal, routed, worker


def reject_external_session_adoption(
    *,
    session_hint: str,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore | None = None,
    proposal_event: TaskEvent | None = None,
) -> TaskEvent | None:
    """Dismiss an external session adoption proposal.

    Soft-deletes the worker bound to the session hint so future updates
    from that session stop generating events.
    """
    if worker_store is not None:
        if proposal_event is not None and proposal_event.task_id is not None:
            worker = worker_store.find_worker_by_target_task(proposal_event.task_id, session_hint)
        else:
            worker = worker_store.get_by_target_id(session_hint)
        if worker is not None:
            worker_store.update_worker(worker.id, state="deleted")
    if proposal_event is None:
        return None
    return task_event_store.update_event(proposal_event.id, state="dismissed")


# ── Turn-finish event for adopted internal sessions ────────────────


def emit_turn_finished_event_unbound(
    *,
    session_id: str,
    conv: Conversation,
    owner_user_id: str,
) -> None:
    """Emit a ``session.turn.finished`` event with no task binding.

    Used when a session has a pending orphan event (broker is triaging
    adoption) and finishes another turn. The event is born
    ``awaiting_grouping`` so the broker packager picks it up and the
    broker routes it to the task once adoption completes.
    """
    if _context is None:
        return
    # Dedup: skip if there's already a pending (awaiting_grouping or routed)
    # turn-finished event for this session — the broker hasn't processed the
    # previous one yet, so a new turn just updates the existing signal.
    existing = _context.task_event_store.list_events(
        event_type=SESSION_TURN_FINISHED_EVENT_TYPE,
    )
    for ev in existing:
        if ev.source_key == session_id and ev.state in ("awaiting_grouping", "routed"):
            return
    session_title = conv.title if conv is not None else session_id
    project_name = _project_name(
        _context.project_store,
        conv.project_id if conv is not None else None,
        owner_user_id or None,
    )
    payload = json.dumps(
        {
            "session_id": session_id,
            "session_title": session_title,
            "project_name": project_name,
            "status": "idle",
        },
        ensure_ascii=False,
    )
    title = f"Session turn finished: {session_title}"
    try:
        _context.task_event_store.create_event(
            uuid.uuid4().hex,
            SESSION_TURN_FINISHED_EVENT_TYPE,
            title,
            source="adoption",
            source_key=session_id,
            state="awaiting_grouping",
            payload=payload,
            tags=[tag] if (tag := host_tag(conv.host_id if conv is not None else None)) else [],
            owner_user_id=owner_user_id,
        )
    except Exception:
        _logger.exception(
            "failed to emit %s event for session %s (unbound)",
            SESSION_TURN_FINISHED_EVENT_TYPE,
            session_id,
        )


def emit_turn_finished_event(
    *,
    session_id: str,
    status: str = "idle",
) -> None:
    """Broadcast a ``session.turn.finished`` event to a session's managers.

    A session is shared: many worker lanes may bind to it, each serving
    tasks of the same owner. The traversal is workers bound to the session
    → their tasks (live only) → the deduped set of those tasks' managers.
    One born-``routed`` event per manager wakes each governing manager;
    the broker is never involved.
    """
    if _context is None:
        return
    workers = _context.worker_store.list_workers_by_target_id(session_id)
    if not workers:
        return

    # Workers → their tasks (live only; deleted lanes and archived tasks
    # must not wake managers).
    tasks_by_id: dict[str, Task] = {}
    for worker in workers:
        if worker.state == "deleted":
            continue
        task = _context.task_store.get(worker.task_id)
        if task is not None and task.state in _LIVE_TASK_STATES:
            tasks_by_id[task.id] = task
    if not tasks_by_id:
        return

    # Tasks → deduped manager set, resolved through the manager registry so
    # each event is attributed to the manager's own owner (queue grouping).
    manager_owner: dict[str, str | None] = {}
    for task in tasks_by_id.values():
        if task.manager_id is None or task.manager_id in manager_owner:
            continue
        manager = (
            _context.manager_store.get(task.manager_id)
            if _context.manager_store is not None
            else None
        )
        if manager is None:
            _logger.warning(
                "turn-finished broadcast: task %s references manager %s "
                "which does not exist; skipping",
                task.id,
                task.manager_id,
            )
            continue
        manager_owner[manager.id] = manager.owner_user_id
    if not manager_owner:
        return

    conv = _context.conversation_store.get_conversation(session_id)
    session_title = conv.title if conv is not None else session_id
    # The session's project is owned by the task owner (sessions are created
    # under the task owner's identity).
    task_owner = next(iter(tasks_by_id.values())).owner_user_id
    project_name = _project_name(
        _context.project_store,
        conv.project_id if conv is not None else None,
        task_owner,
    )
    payload = json.dumps(
        {
            "session_id": session_id,
            "session_title": session_title,
            "project_name": project_name,
            "status": status,
        },
        ensure_ascii=False,
    )
    title = f"Session turn finished: {session_title}"

    # Idempotency: a turn-finished event is a signal, not a log. Skip a
    # manager that already has an unconsumed one for this session — the
    # manager triages the pending signal on its next notice turn.
    pending_managers = {
        ev.manager_id
        for ev in _context.task_event_store.list_events(
            event_type=SESSION_TURN_FINISHED_EVENT_TYPE
        )
        if ev.source_key == session_id
        and ev.state in ("awaiting_grouping", "routed")
        and ev.manager_id is not None
    }

    for manager_id, owner in manager_owner.items():
        if manager_id in pending_managers:
            continue
        try:
            event = _context.task_event_store.create_event(
                uuid.uuid4().hex,
                SESSION_TURN_FINISHED_EVENT_TYPE,
                title,
                manager_id=manager_id,
                source="adoption",
                source_key=session_id,
                state="routed",
                payload=payload,
                owner_user_id=owner or "__anonymous__",
            )
            _context.task_event_store.update_event(event.id, routed_at=now_epoch())
        except Exception:
            _logger.exception(
                "failed to emit %s event for session %s to manager %s",
                SESSION_TURN_FINISHED_EVENT_TYPE,
                session_id,
                manager_id,
            )


# ── Session-deleted event for adopted internal sessions ────────────


def emit_session_deleted_event_for_pending_turn(
    *,
    session_id: str,
    conv: Conversation | None,
    owner_user_id: str,
) -> None:
    """Emit a ``session.deleted`` event only when a turn-finished is in flight.

    Unbound sessions are otherwise untracked — no worker lane, no task, no
    manager — so a deletion signal with no pending turn-finished is pure
    noise. When a ``session.turn.finished`` event IS pending, the deletion
    must ride the same lanes it is traveling: born-``routed`` to each
    manager already holding a routed one (the manager packager batches
    per-session events, so the deletion supersedes the phantom turn), and
    born-``awaiting_grouping`` when one is still awaiting broker grouping.
    """
    if _context is None:
        return
    # Where are this session's in-flight turn-finished signals? Filter by
    # state in the store: settled events accumulate forever, so a type-only
    # scan would grow unbounded.
    managers_holding_turn: set[str] = set()
    turn_awaiting_broker = False
    for state in ("awaiting_grouping", "routed"):
        for ev in _context.task_event_store.list_events(
            event_type=SESSION_TURN_FINISHED_EVENT_TYPE,
            state=state,
        ):
            if ev.source_key != session_id:
                continue
            if ev.manager_id is not None:
                managers_holding_turn.add(ev.manager_id)
            elif state == "awaiting_grouping":
                turn_awaiting_broker = True
    if not managers_holding_turn and not turn_awaiting_broker:
        return

    session_title = conv.title if conv is not None else session_id
    project_name = _project_name(
        _context.project_store,
        conv.project_id if conv is not None else None,
        owner_user_id or None,
    )
    payload = json.dumps(
        {
            "session_id": session_id,
            "session_title": session_title,
            "project_name": project_name,
        },
        ensure_ascii=False,
    )
    title = f"Session deleted: {session_title}"

    # Idempotency: skip a destination that already holds an unconsumed
    # deleted signal for this session (double-delete race). Filter by state
    # in the store for the same unbounded-growth reason as above.
    pending_deleted_managers: set[str] = set()
    deleted_awaiting_broker = False
    for state in ("awaiting_grouping", "routed"):
        for ev in _context.task_event_store.list_events(
            event_type=SESSION_DELETED_EVENT_TYPE,
            state=state,
        ):
            if ev.source_key != session_id:
                continue
            if ev.manager_id is not None:
                pending_deleted_managers.add(ev.manager_id)
            else:
                deleted_awaiting_broker = True

    if turn_awaiting_broker and not deleted_awaiting_broker:
        try:
            _context.task_event_store.create_event(
                uuid.uuid4().hex,
                SESSION_DELETED_EVENT_TYPE,
                title,
                source="adoption",
                source_key=session_id,
                state="awaiting_grouping",
                payload=payload,
                tags=[tag]
                if (tag := host_tag(conv.host_id if conv is not None else None))
                else [],
                owner_user_id=owner_user_id,
            )
        except Exception:
            _logger.exception(
                "failed to emit %s event for session %s (unbound)",
                SESSION_DELETED_EVENT_TYPE,
                session_id,
            )

    # Managers already holding a routed turn-finished for the deleted
    # session get a born-``routed`` deletion attributed to their own owner,
    # so it lands in the same manager queue (and batch) as the signal it
    # supersedes.
    for manager_id in managers_holding_turn - pending_deleted_managers:
        manager = (
            _context.manager_store.get(manager_id) if _context.manager_store is not None else None
        )
        if manager is None:
            _logger.warning(
                "session-deleted event: in-flight turn-finished references "
                "manager %s which does not exist; skipping",
                manager_id,
            )
            continue
        try:
            event = _context.task_event_store.create_event(
                uuid.uuid4().hex,
                SESSION_DELETED_EVENT_TYPE,
                title,
                manager_id=manager_id,
                source="adoption",
                source_key=session_id,
                state="routed",
                payload=payload,
                owner_user_id=manager.owner_user_id or "__anonymous__",
            )
            _context.task_event_store.update_event(event.id, routed_at=now_epoch())
        except Exception:
            _logger.exception(
                "failed to emit %s event for session %s to manager %s",
                SESSION_DELETED_EVENT_TYPE,
                session_id,
                manager_id,
            )


def emit_session_deleted_event(
    *,
    session_id: str,
    conv: Conversation | None = None,
) -> None:
    """Broadcast a ``session.deleted`` event to a session's managers.

    Same traversal as the turn-finished broadcast: workers bound to the
    session → their tasks (live only) → the deduped set of those tasks'
    managers. One born-``routed`` event per manager; the broker is never
    involved. Call before the session's workers are soft-deleted — deleted
    lanes are skipped by the traversal. ``conv`` is the pre-delete row when
    the caller still holds it (the store row is already gone after
    ``delete_conversation``).
    """
    if _context is None:
        return
    workers = _context.worker_store.list_workers_by_target_id(session_id)
    if not workers:
        return

    # Workers → their tasks (live only; deleted lanes and archived tasks
    # must not wake managers).
    tasks_by_id: dict[str, Task] = {}
    for worker in workers:
        if worker.state == "deleted":
            continue
        task = _context.task_store.get(worker.task_id)
        if task is not None and task.state in _LIVE_TASK_STATES:
            tasks_by_id[task.id] = task
    if not tasks_by_id:
        return

    # Tasks → deduped manager set, resolved through the manager registry so
    # each event is attributed to the manager's own owner (queue grouping).
    manager_owner: dict[str, str | None] = {}
    for task in tasks_by_id.values():
        if task.manager_id is None or task.manager_id in manager_owner:
            continue
        manager = (
            _context.manager_store.get(task.manager_id)
            if _context.manager_store is not None
            else None
        )
        if manager is None:
            _logger.warning(
                "session-deleted broadcast: task %s references manager %s "
                "which does not exist; skipping",
                task.id,
                task.manager_id,
            )
            continue
        manager_owner[manager.id] = manager.owner_user_id
    if not manager_owner:
        return

    if conv is None:
        conv = _context.conversation_store.get_conversation(session_id)
    session_title = conv.title if conv is not None else session_id
    # The session's project is owned by the task owner (sessions are created
    # under the task owner's identity).
    task_owner = next(iter(tasks_by_id.values())).owner_user_id
    project_name = _project_name(
        _context.project_store,
        conv.project_id if conv is not None else None,
        task_owner,
    )
    payload = json.dumps(
        {
            "session_id": session_id,
            "session_title": session_title,
            "project_name": project_name,
        },
        ensure_ascii=False,
    )
    title = f"Session deleted: {session_title}"

    # Idempotency: skip a manager that already has an unconsumed deleted
    # signal for this session (double-delete race). A pending turn-finished
    # signal does NOT suppress this one — deletion supersedes it. Filter by
    # state in the store: settled events accumulate forever, so a type-only
    # scan would grow unbounded.
    pending_managers: set[str] = set()
    for state in ("awaiting_grouping", "routed"):
        for ev in _context.task_event_store.list_events(
            event_type=SESSION_DELETED_EVENT_TYPE,
            state=state,
        ):
            if ev.source_key == session_id and ev.manager_id is not None:
                pending_managers.add(ev.manager_id)

    for manager_id, owner in manager_owner.items():
        if manager_id in pending_managers:
            continue
        try:
            event = _context.task_event_store.create_event(
                uuid.uuid4().hex,
                SESSION_DELETED_EVENT_TYPE,
                title,
                manager_id=manager_id,
                source="adoption",
                source_key=session_id,
                state="routed",
                payload=payload,
                owner_user_id=owner or "__anonymous__",
            )
            _context.task_event_store.update_event(event.id, routed_at=now_epoch())
        except Exception:
            _logger.exception(
                "failed to emit %s event for session %s to manager %s",
                SESSION_DELETED_EVENT_TYPE,
                session_id,
                manager_id,
            )
