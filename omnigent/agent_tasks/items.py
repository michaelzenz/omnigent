"""TaskItem lifecycle — inbox, reconcile, and user resolve."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Literal

from omnigent.agent_tasks.bootstrap import (
    bootstrap_task_manager,
    resolve_bootstrap_params,
)
from omnigent.agent_tasks.dispatch import dispatch_worker_for_item, resolve_dispatch_params
from omnigent.agent_tasks.task_activity import sync_task_activity_state
from omnigent.agent_tasks.workers import worker_for_item
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskEventExecution, TaskItem
from omnigent.entities.agent_queue import AgentQueueKey
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore

ItemResolution = Literal["accept_item", "edit_and_dispatch", "reject_item"]
_INBOX_STATES = frozenset({"pending"})
# Editable while the work is waiting: before it is handed over, and after it is
# parked. A parked item is stopped precisely so its instructions can be fixed
# before the retry.
_EDITABLE_WORK_ITEM_STATES = frozenset({"queued", "interrupted", "dispatch_failed"})


def _generate_item_id() -> str:
    return uuid.uuid4().hex


def _merge_payload(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    if overrides:
        merged.update(overrides)
    return merged


def _item_dispatch_payload(item: TaskItem) -> dict[str, Any]:
    return {
        "instructions": item.instructions or "",
        "internal_note": item.internal_note,
    }


def create_task_item(
    *,
    task: Task,
    task_item_store: TaskItemStore,
    worker_store: WorkerStore,
    title: str,
    state: str = "draft",
    description: str | None = None,
    instructions: str | None = None,
    internal_note: str | None = None,
    worker_id: str | None = None,
    created_by: str = "manager",
    event_ids: list[str] | None = None,
    task_event_store: TaskEventStore | None = None,
) -> TaskItem:
    """Create a task item and optionally link contributing events."""
    item = task_item_store.create_item(
        _generate_item_id(),
        task.id,
        title,
        state=state,
        description=description,
        instructions=instructions,
        internal_note=internal_note,
        created_by=created_by,
    )
    if worker_id is not None:
        worker = worker_store.get_worker(worker_id)
        if worker is None or worker.task_id != task.id:
            raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
        item = task_item_store.update_item(item.id, worker_id=worker_id)
    if event_ids and task_event_store is not None:
        for event_id in event_ids:
            task_item_store.link_event(item.id, event_id)
            task_event_store.update_event(
                event_id,
                state="reconciled",
                processed_at=now_epoch(),
            )
    return item


async def ensure_task_manager_for_dispatch(
    *,
    task: Task,
    task_store: TaskStore,
    conversation_store: ConversationStore,
    role_profile: TaskRoleProfile | None = None,
    host_id: str | None = None,
    workspace: str | None = None,
    harness: str | None = None,
    model: str | None = None,
    session_creator: Any | None = None,
    app_state: Any | None = None,
    user_id: str | None = None,
) -> Task:
    """Ensure a manager session exists before dispatch."""
    if task.state == "pending":
        raise OmnigentError(
            "Accept the task package before dispatching work",
            code=ErrorCode.CONFLICT,
        )
    if task.manager_conversation_id is not None:
        existing = await asyncio.to_thread(
            conversation_store.get_conversation,
            task.manager_conversation_id,
        )
        if existing is None:
            raise OmnigentError(
                "Manager session is missing; clear manager_conversation_id before re-bootstrap",
                code=ErrorCode.CONFLICT,
            )
        return task

    params = resolve_bootstrap_params(
        host_id=host_id,
        workspace=workspace,
        harness=harness,
        model=model,
        role_profile=role_profile,
    )
    return await bootstrap_task_manager(
        task=task,
        task_store=task_store,
        conversation_store=conversation_store,
        params=params,
        session_creator=session_creator,
        app_state=app_state,
        user_id=user_id,
    )


def submit_item_for_user_ack(task_item_store: TaskItemStore, item_id: str) -> TaskItem:
    """Move a draft item into the user inbox."""
    updated = task_item_store.update_item(item_id, state="pending")
    if updated is None:
        raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
    return updated


def reject_task_item(*, item: TaskItem, task_item_store: TaskItemStore) -> TaskItem:
    """Cancel a user-inbox task item without dispatching."""
    if item.state not in _INBOX_STATES:
        raise OmnigentError(
            f"Cannot resolve item in state {item.state!r}",
            code=ErrorCode.CONFLICT,
        )
    updated = task_item_store.update_item(item.id, state="cancelled")
    if updated is None:
        raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
    return updated


async def resolve_task_item(
    *,
    item: TaskItem,
    resolution: ItemResolution,
    task: Task,
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    edited_payload: dict[str, Any] | None = None,
    role_profile: TaskRoleProfile | None = None,
    agent_queue_store: AgentQueueStore | None = None,
    owner_user_id: str | None = None,
    session_creator: Any | None = None,
    app_state: Any | None = None,
    user_id: str | None = None,
) -> tuple[TaskItem, TaskEventExecution | None]:
    """Accept, edit, or reject a user-inbox task item.

    Accept/edit-and-dispatch no longer launch a worker synchronously when an
    agent queue store is wired: the item moves to ``queued`` and an
    ``item.dispatch`` queue item is enqueued for the worker slot, and the
    dispatcher spawns the worker session off the request path. When no queue
    store is provided (single-process tests, older setups) the legacy
    synchronous dispatch path is used so behaviour is preserved.
    """
    if item.state not in _INBOX_STATES:
        raise OmnigentError(
            f"Cannot resolve item in state {item.state!r}",
            code=ErrorCode.CONFLICT,
        )
    if resolution == "reject_item":
        updated = await asyncio.to_thread(
            reject_task_item,
            item=item,
            task_item_store=task_item_store,
        )
        return updated, None

    payload = _merge_payload(_item_dispatch_payload(item), edited_payload)
    task = await ensure_task_manager_for_dispatch(
        task=task,
        task_store=task_store,
        conversation_store=conversation_store,
        role_profile=role_profile,
        host_id=str(payload.get("host_id")) if payload.get("host_id") is not None else None,
        workspace=str(payload.get("workspace")) if payload.get("workspace") is not None else None,
        harness=str(payload.get("harness")) if payload.get("harness") is not None else None,
        model=str(payload.get("model")) if payload.get("model") is not None else None,
        session_creator=session_creator,
        app_state=app_state,
        user_id=user_id,
    )
    if resolution == "edit_and_dispatch":
        update_kwargs: dict[str, Any] = {
            "title": str(payload.get("title", item.title)),
            "instructions": str(payload.get("instructions", item.instructions or "")),
        }
        if edited_payload is not None and "description" in edited_payload:
            update_kwargs["description"] = str(payload.get("description") or "")
        if edited_payload is not None and "internal_note" in edited_payload:
            update_kwargs["internal_note"] = str(payload.get("internal_note") or "")
        await asyncio.to_thread(task_item_store.update_item, item.id, **update_kwargs)
        refreshed = await asyncio.to_thread(task_item_store.get_item, item.id)
        assert refreshed is not None
        item = refreshed
        payload = _merge_payload(_item_dispatch_payload(item), edited_payload)

    worker = worker_for_item(item, worker_store=worker_store)
    if worker is None:
        raise OmnigentError(
            "Item has no worker lane; assign one before resolving",
            code=ErrorCode.CONFLICT,
        )

    params = resolve_dispatch_params(
        payload=payload,
        role_profile=role_profile,
        host_id=str(payload.get("host_id")) if payload.get("host_id") is not None else None,
        workspace=str(payload.get("workspace")) if payload.get("workspace") is not None else None,
        harness=str(payload.get("harness")) if payload.get("harness") is not None else None,
        model=str(payload.get("model")) if payload.get("model") is not None else None,
    )
    if agent_queue_store is not None:
        # Phase 4: enqueue for the worker slot; the dispatcher launches the
        # worker session off the request path. No execution/runner here.
        updated = await asyncio.to_thread(
            task_item_store.update_item,
            item.id,
            state="queued",
        )
        assert updated is not None
        queue_payload = payload
        await asyncio.to_thread(
            agent_queue_store.enqueue,
            uuid.uuid4().hex,
            AgentQueueKey(
                role="worker",
                owner_user_id=owner_user_id or task.owner_user_id or "__anonymous__",
                scope_id=worker.id,
            ),
            kind="item.dispatch",
            source_ids=[item.id],
            payload=json.dumps(queue_payload),
        )
        task = sync_task_activity_state(
            task,
            task_store=task_store,
            task_item_store=task_item_store,
        )
        return updated, None

    execution, _worker_id = await dispatch_worker_for_item(
        task=task,
        item=item,
        params=params,
        task_store=task_store,
        task_item_store=task_item_store,
        task_event_store=task_event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        session_creator=session_creator,
        app_state=app_state,
        user_id=user_id,
    )
    updated = await asyncio.to_thread(task_item_store.get_item, item.id)
    assert updated is not None
    task = sync_task_activity_state(
        task,
        task_store=task_store,
        task_item_store=task_item_store,
    )
    return updated, execution


def reconcile_events(
    *,
    task: Task,
    event_ids: list[str],
    task_event_store: TaskEventStore,
) -> list[TaskEvent]:
    """Mark routed events reconciled without creating items."""
    reconciled: list[TaskEvent] = []
    for event_id in event_ids:
        event = task_event_store.get_event(event_id)
        if event is None or event.task_id != task.id:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        if event.state != "routed":
            raise OmnigentError(
                f"Cannot reconcile event in state {event.state!r}",
                code=ErrorCode.CONFLICT,
            )
        updated = task_event_store.update_event(
            event_id,
            state="reconciled",
            processed_at=now_epoch(),
        )
        if updated is not None:
            reconciled.append(updated)
    return reconciled


def patch_task_item(
    *,
    item: TaskItem,
    task_item_store: TaskItemStore,
    worker_store: WorkerStore,
    title: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    internal_note: str | None = None,
    worker_id: str | None = None,
) -> TaskItem:
    """Update a queued work item before it is dispatched."""
    if item.state not in _EDITABLE_WORK_ITEM_STATES:
        raise OmnigentError(
            f"Cannot edit task item in state {item.state!r}",
            code=ErrorCode.CONFLICT,
        )
    if title is not None and not title.strip():
        raise OmnigentError("title must be a non-empty string", code=ErrorCode.INVALID_INPUT)
    updated = task_item_store.update_item(
        item.id,
        title=title.strip() if title is not None else None,
        description=description,
        instructions=instructions,
        internal_note=internal_note,
    )
    if updated is None:
        raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
    if worker_id is not None:
        worker = worker_store.get_worker(worker_id)
        if worker is None or worker.task_id != item.task_id:
            raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
        updated = task_item_store.update_item(item.id, worker_id=worker_id)
    return updated
