"""TaskItem lifecycle — inbox, reconcile, and user resolve."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from omnigent.agent_tasks.dispatch import dispatch_worker_for_item, resolve_dispatch_params
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskEventExecution, TaskItem
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore

ItemResolution = Literal["accept_item", "edit_and_dispatch", "reject_item"]
_INBOX_STATES = frozenset({"awaiting_user_ack"})
_EDITABLE_WORK_ITEM_STATES = frozenset({"queued", "approved"})


def _generate_item_id() -> str:
    return uuid.uuid4().hex


def _merge_payload(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    if overrides:
        merged.update(overrides)
    return merged


def _item_dispatch_payload(item: TaskItem) -> dict[str, Any]:
    return {
        "worker_agent_id": item.worker_agent_id,
        "title": item.title,
        "instructions": item.instructions or "",
        "host_id": item.host_id,
        "workspace": item.workspace,
        "harness": item.harness,
        "model": item.model,
    }


def create_task_item(
    *,
    task: Task,
    task_item_store: TaskItemStore,
    title: str,
    state: str = "draft",
    instructions: str | None = None,
    worker_agent_id: str | None = None,
    model: str | None = None,
    host_id: str | None = None,
    workspace: str | None = None,
    harness: str | None = None,
    priority: int = 0,
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
        instructions=instructions,
        worker_agent_id=worker_agent_id,
        model=model,
        host_id=host_id,
        workspace=workspace,
        harness=harness,
        priority=priority,
        created_by=created_by,
    )
    if event_ids and task_event_store is not None:
        for event_id in event_ids:
            task_item_store.link_event(item.id, event_id)
            task_event_store.update_event(
                event_id,
                state="reconciled",
                processed_at=now_epoch(),
            )
    return item


def submit_item_for_user_ack(task_item_store: TaskItemStore, item_id: str) -> TaskItem:
    """Move a draft item into the user inbox."""
    updated = task_item_store.update_item(item_id, state="awaiting_user_ack")
    if updated is None:
        raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
    return updated


def resolve_task_item(
    *,
    item: TaskItem,
    resolution: ItemResolution,
    task: Task,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    edited_payload: dict[str, Any] | None = None,
    secretary_profile: UserSecretaryProfile | None = None,
) -> tuple[TaskItem, TaskEventExecution | None]:
    """Accept, edit, or reject a user-inbox task item."""
    if item.state not in _INBOX_STATES:
        raise OmnigentError(
            f"Cannot resolve item in state {item.state!r}",
            code=ErrorCode.CONFLICT,
        )
    if resolution == "reject_item":
        updated = task_item_store.update_item(item.id, state="cancelled")
        if updated is None:
            raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
        return updated, None

    payload = _merge_payload(_item_dispatch_payload(item), edited_payload)
    if resolution == "edit_and_dispatch":
        task_item_store.update_item(
            item.id,
            title=str(payload.get("title", item.title)),
            instructions=str(payload.get("instructions", item.instructions or "")),
            worker_agent_id=str(payload.get("worker_agent_id", item.worker_agent_id or "")),
            model=str(payload.get("model")) if payload.get("model") is not None else item.model,
            host_id=str(payload.get("host_id")) if payload.get("host_id") is not None else item.host_id,
            workspace=str(payload.get("workspace"))
            if payload.get("workspace") is not None
            else item.workspace,
            harness=str(payload.get("harness")) if payload.get("harness") is not None else item.harness,
        )
        refreshed = task_item_store.get_item(item.id)
        assert refreshed is not None
        item = refreshed

    params = resolve_dispatch_params(payload=payload, secretary_profile=secretary_profile)
    execution, _worker_id = dispatch_worker_for_item(
        task=task,
        item=item,
        params=params,
        task_item_store=task_item_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
    )
    updated = task_item_store.update_item(item.id, state="running")
    assert updated is not None
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
    title: str | None = None,
    instructions: str | None = None,
    worker_agent_id: str | None = None,
    model: str | None = None,
    host_id: str | None = None,
    workspace: str | None = None,
    harness: str | None = None,
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
        instructions=instructions,
        worker_agent_id=worker_agent_id,
        model=model,
        host_id=host_id,
        workspace=workspace,
        harness=harness,
    )
    if updated is None:
        raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
    return updated
