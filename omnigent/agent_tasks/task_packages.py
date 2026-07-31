"""Pending task packages — secretary reconcile and user inbox ack."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_AGENT_NAME, resolve_task_agent_id
from omnigent.agent_tasks.constants import AMBIGUOUS_EVENT_STATES
from omnigent.agent_tasks.items import create_task_item
from omnigent.agent_tasks.task_match import (
    internal_note_from_event_tags,
    collect_event_tags,
    task_tags_from_event_tags,
)
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskItem, TaskTag
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore

_CLAIMABLE_EVENT_STATES = frozenset(AMBIGUOUS_EVENT_STATES)


def _generate_task_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class PackageItemSpec:
    """One backlog item to create on a pending task package."""

    title: str
    event_ids: list[str]
    description: str | None = None
    instructions: str | None = None
    internal_note: str | None = None
    item_id: str | None = None


def _claimable_events(
    event_ids: list[str],
    *,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
) -> list[TaskEvent]:
    claimed: list[TaskEvent] = []
    for event_id in event_ids:
        if task_item_store.get_item_for_event(event_id) is not None:
            continue
        if task_item_store.get_fyi_cluster_for_event(event_id) is not None:
            continue
        event = task_event_store.get_event(event_id)
        if event is None:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        if event.state not in _CLAIMABLE_EVENT_STATES:
            continue
        claimed.append(event)
    return claimed


def _require_pending_package_task(task: Task) -> None:
    if task.state != "pending":
        raise OmnigentError(
            "Task package reconcile requires a pending task",
            code=ErrorCode.CONFLICT,
        )


def reconcile_events_to_task(
    *,
    task: Task,
    spec: PackageItemSpec,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
) -> TaskItem | None:
    """Create or extend a task item on a pending package and reconcile events."""
    _require_pending_package_task(task)
    events = _claimable_events(
        spec.event_ids,
        task_event_store=task_event_store,
        task_item_store=task_item_store,
    )
    if not events:
        return None

    if spec.item_id is not None:
        existing = task_item_store.get_item(spec.item_id)
        if existing is None or existing.task_id != task.id:
            raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
        if existing.state != "awaiting_user_ack":
            raise OmnigentError(
                f"Cannot extend item in state {existing.state!r}",
                code=ErrorCode.CONFLICT,
            )
        updated = task_item_store.update_item(
            spec.item_id,
            title=spec.title,
            description=spec.description,
            instructions=spec.instructions,
            internal_note=spec.internal_note,
        )
        assert updated is not None
        item = updated
        for event in events:
            task_item_store.link_event(item.id, event.id, relation="triggered")
            task_event_store.update_event(
                event.id,
                state="reconciled",
                processed_at=now_epoch(),
            )
        return item

    return create_task_item(
        task=task,
        task_item_store=task_item_store,
        task_event_store=task_event_store,
        title=spec.title,
        description=spec.description,
        instructions=spec.instructions,
        internal_note=spec.internal_note,
        state="awaiting_user_ack",
        created_by="secretary",
        event_ids=[event.id for event in events],
    )


def create_task_package(
    *,
    owner_user_id: str,
    manager_agent_id: str,
    title: str,
    items: list[PackageItemSpec],
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    task_id: str | None = None,
    internal_note: str | None = None,
    description: str | None = None,
    tags: list[TaskTag] | None = None,
    event_tags: list | None = None,
) -> Task:
    """Create a pending task package with secretary-reconciled items."""
    if not items:
        raise OmnigentError("At least one item is required", code=ErrorCode.INVALID_INPUT)

    resolved_task_id = task_id or _generate_task_id()
    resolved_tags = list(tags or [])
    if not resolved_tags and event_tags:
        resolved_tags = task_tags_from_event_tags(resolved_task_id, event_tags)
    resolved_internal_note = internal_note or (
        internal_note_from_event_tags(event_tags) if event_tags else None
    )

    task = task_store.create(
        resolved_task_id,
        manager_agent_id,
        title,
        owner_user_id=owner_user_id,
        description=description,
        internal_note=resolved_internal_note,
        state="pending",
        tags=resolved_tags,
    )

    for spec in items:
        created = reconcile_events_to_task(
            task=task,
            spec=spec,
            task_item_store=task_item_store,
            task_event_store=task_event_store,
        )
        if created is None:
            raise OmnigentError(
                "No claimable ambiguous events for task package item",
                code=ErrorCode.CONFLICT,
            )
    return task_store.get(resolved_task_id) or task


def reject_task_package(
    *,
    task: Task,
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
) -> Task:
    """Archive a pending package and release its events back to the secretary queue."""
    _require_pending_package_task(task)

    for item in task_item_store.list_items_for_task(task.id):
        for link in task_item_store.list_events_for_item(item.id):
            event = task_event_store.get_event(link.event_id)
            if event is not None and event.state == "reconciled":
                task_event_store.update_event(link.event_id, state="awaiting_grouping")
        if item.state != "cancelled":
            task_item_store.update_item(item.id, state="cancelled")

    archived = task_store.update(task.id, state="archived")
    assert archived is not None
    return archived


def resolve_manager_agent_id(agent_store: AgentStore, manager_agent_id: str | None) -> str:
    if manager_agent_id:
        return manager_agent_id
    return resolve_task_agent_id(agent_store, TASK_MANAGER_AGENT_NAME)
