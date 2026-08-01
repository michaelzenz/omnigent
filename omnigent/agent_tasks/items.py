"""TaskItem lifecycle — inbox, reconcile, and user resolve."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from omnigent.agent_tasks.bootstrap import (
    bootstrap_task_manager,
    resolve_bootstrap_params,
)
from omnigent.agent_tasks.task_activity import sync_task_activity_state
from omnigent.agent_tasks.dispatch import dispatch_worker_for_item, resolve_dispatch_params
from omnigent.agent_tasks.workers import assign_worker_profile, worker_for_item
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.task_store import TaskStore
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskEventExecution, TaskItem
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.worker_store import WorkerStore

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
        "title": item.title,
        "instructions": item.instructions or "",
        "internal_note": item.internal_note,
    }


def _profile_id_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("worker_profile_id", "worker_agent_id"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


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
    worker_profile_id: str | None = None,
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
    profile_id = worker_profile_id.strip() if worker_profile_id else None
    if profile_id:
        item, _worker = assign_worker_profile(
            item=item,
            profile_id=profile_id,
            worker_store=worker_store,
            task_item_store=task_item_store,
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


def ensure_task_manager_for_dispatch(
    *,
    task: Task,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    secretary_profile: UserSecretaryProfile | None = None,
    host_id: str | None = None,
    workspace: str | None = None,
    harness: str | None = None,
    model: str | None = None,
) -> Task:
    """Activate pending packages and ensure a manager session exists before dispatch."""
    if task.state == "pending":
        activated = task_store.update(task.id, state="idle")
        if activated is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
        task = activated

    params = resolve_bootstrap_params(
        host_id=host_id,
        workspace=workspace,
        harness=harness,
        model=model,
        secretary_profile=secretary_profile,
    )
    return bootstrap_task_manager(
        task=task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
        params=params,
    )


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
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
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
    task = ensure_task_manager_for_dispatch(
        task=task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
        secretary_profile=secretary_profile,
        host_id=str(payload.get("host_id")) if payload.get("host_id") is not None else None,
        workspace=str(payload.get("workspace")) if payload.get("workspace") is not None else None,
        harness=str(payload.get("harness")) if payload.get("harness") is not None else None,
        model=str(payload.get("model")) if payload.get("model") is not None else None,
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
        task_item_store.update_item(item.id, **update_kwargs)
        refreshed = task_item_store.get_item(item.id)
        assert refreshed is not None
        item = refreshed
        payload = _merge_payload(_item_dispatch_payload(item), edited_payload)

    profile_id = _profile_id_from_payload(payload)
    if profile_id is not None:
        item, worker = assign_worker_profile(
            item=item,
            profile_id=profile_id,
            worker_store=worker_store,
            task_item_store=task_item_store,
        )
    else:
        worker = worker_for_item(item, worker_store=worker_store)
        if worker is None:
            raise OmnigentError(
                "worker_profile_id is required for unassigned inbox items",
                code=ErrorCode.INVALID_INPUT,
            )

    params = resolve_dispatch_params(
        payload={**payload, "worker_profile_id": worker.profile_id},
        secretary_profile=secretary_profile,
        host_id=str(payload.get("host_id")) if payload.get("host_id") is not None else None,
        workspace=str(payload.get("workspace")) if payload.get("workspace") is not None else None,
        harness=str(payload.get("harness")) if payload.get("harness") is not None else None,
        model=str(payload.get("model")) if payload.get("model") is not None else None,
    )
    execution, _worker_id = dispatch_worker_for_item(
        task=task,
        item=item,
        params=params,
        task_store=task_store,
        task_item_store=task_item_store,
        task_event_store=task_event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
    )
    updated = task_item_store.get_item(item.id)
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
    worker_profile_id: str | None = None,
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
    if worker_profile_id is not None and worker_profile_id.strip():
        updated, _worker = assign_worker_profile(
            item=updated,
            profile_id=worker_profile_id,
            worker_store=worker_store,
            task_item_store=task_item_store,
        )
    return updated
