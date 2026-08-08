"""Pending task packages — broker reconcile and user inbox ack."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from omnigent.agent_tasks.constants import AMBIGUOUS_EVENT_STATES
from omnigent.agent_tasks.items import create_task_item
from omnigent.agent_tasks.role_keys import (
    MANAGER_DEFAULT_ROLE_KEY,
    MANAGER_ROLE_PREFIX,
    WORKER_DEFAULT_ROLE_KEY,
    is_manager_role_key,
)
from omnigent.agent_tasks.task_match import (
    internal_note_from_event_tags,
    task_tags_from_event_tags,
)
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskItem, TaskTag
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore

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


def _bulk_claimable_events(
    event_ids: list[str],
    *,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
) -> list[TaskEvent]:
    """Return the claimable ambiguous events for ``event_ids`` in one read pass.

    Loads every referenced event in one query and the two claim sets (events
    already linked to a task item or an open FYI cluster) in two more, then
    filters in memory. A missing event id raises ``NOT_FOUND`` to match the
    per-spec path. Duplicates across the input are de-duplicated.
    """
    unique_ids: list[str] = []
    seen: set[str] = set()
    for event_id in event_ids:
        if event_id not in seen:
            seen.add(event_id)
            unique_ids.append(event_id)
    if not unique_ids:
        return []
    events_by_id = {e.id: e for e in task_event_store.get_events(unique_ids)}
    if any(eid not in events_by_id for eid in unique_ids):
        raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
    claimed_by_items = task_item_store.get_event_ids_claimed_by_items(unique_ids)
    claimed_by_fyi = task_item_store.get_event_ids_claimed_by_fyi_clusters(unique_ids)
    claimable: list[TaskEvent] = []
    for eid in unique_ids:
        if eid in claimed_by_items or eid in claimed_by_fyi:
            continue
        event = events_by_id[eid]
        if event.state in _CLAIMABLE_EVENT_STATES:
            claimable.append(event)
    return claimable


def _require_pending_package_task(task: Task) -> None:
    if task.state != "pending":
        raise OmnigentError(
            "Task package reconcile requires a pending task",
            code=ErrorCode.CONFLICT,
        )


def reconcile_events_to_task_batch(
    *,
    task: Task,
    specs: list[PackageItemSpec],
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
) -> list[TaskItem | None]:
    """Create or extend multiple items on a pending package and reconcile events.

    One read pass (events + the two claim sets) feeds every spec, and an event
    claimed by an earlier spec is consumed so a later spec cannot re-claim it
    within the same batch. Returns one entry per spec (``None`` where a spec had
    no claimable events), aligned with ``specs`` — so callers can detect a spec
    that matched nothing without a separate query.
    """
    _require_pending_package_task(task)
    if not specs:
        return []

    all_event_ids = [eid for spec in specs for eid in spec.event_ids]
    events_by_id = {
        e.id: e
        for e in _bulk_claimable_events(
            all_event_ids,
            task_event_store=task_event_store,
            task_item_store=task_item_store,
        )
    }
    # Track which claimable ids are still available; consume as each spec takes them.
    available: set[str] = set(events_by_id)

    results: list[TaskItem | None] = []
    for spec in specs:
        events = [events_by_id[eid] for eid in spec.event_ids if eid in available]
        if not events:
            results.append(None)
            continue
        for eid in spec.event_ids:
            available.discard(eid)

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
            results.append(item)
        else:
            item = create_task_item(
                task=task,
                task_item_store=task_item_store,
                worker_store=worker_store,
                task_event_store=task_event_store,
                title=spec.title,
                description=spec.description,
                instructions=spec.instructions,
                internal_note=spec.internal_note,
                state="awaiting_user_ack",
                created_by="broker",
                event_ids=[event.id for event in events],
            )
            results.append(item)
    return results


def reconcile_events_to_task(
    *,
    task: Task,
    spec: PackageItemSpec,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
) -> TaskItem | None:
    """Create or extend a single task item on a pending package and reconcile events."""
    results = reconcile_events_to_task_batch(
        task=task,
        specs=[spec],
        task_item_store=task_item_store,
        task_event_store=task_event_store,
        worker_store=worker_store,
    )
    return results[0] if results else None


def create_task_package(
    *,
    owner_user_id: str,
    title: str,
    items: list[PackageItemSpec],
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    task_id: str | None = None,
    internal_note: str | None = None,
    description: str | None = None,
    tags: list[TaskTag] | None = None,
    event_tags: list | None = None,
) -> Task:
    """Create a pending task package with broker-reconciled items."""
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
        title,
        owner_user_id=owner_user_id,
        description=description,
        internal_note=resolved_internal_note,
        manager_role_key=MANAGER_DEFAULT_ROLE_KEY,
        worker_role_key=WORKER_DEFAULT_ROLE_KEY,
        state="pending",
        tags=resolved_tags,
    )

    results = reconcile_events_to_task_batch(
        task=task,
        specs=items,
        task_item_store=task_item_store,
        task_event_store=task_event_store,
        worker_store=worker_store,
    )
    if any(result is None for result in results):
        raise OmnigentError(
            "No claimable ambiguous events for task package item",
            code=ErrorCode.CONFLICT,
        )
    return task_store.get(resolved_task_id) or task


def accept_task_package(
    *,
    task: Task,
    task_store: TaskStore,
    task_role_profile_store: TaskRoleProfileStore,
) -> Task:
    """Promote a pending package to an idle task after the manager role is set."""
    _require_pending_package_task(task)
    manager_role_key = (task.manager_role_key or "").strip()
    if not manager_role_key or not is_manager_role_key(manager_role_key):
        raise OmnigentError(
            "manager_role_key must be set to a manager glossary role before accept",
            code=ErrorCode.INVALID_INPUT,
        )
    profile = task_role_profile_store.get(manager_role_key)
    if profile is None:
        raise OmnigentError(
            f"Task role profile not found: {manager_role_key}",
            code=ErrorCode.NOT_FOUND,
        )
    if not profile.agent_profile_id:
        raise OmnigentError(
            "the manager role must name an agent profile before accept",
            code=ErrorCode.INVALID_INPUT,
        )
    activated = task_store.update(task.id, state="idle")
    if activated is None:
        raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
    return activated


def reject_task_package(
    *,
    task: Task,
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
) -> Task:
    """Archive a pending package and release its events back to the broker queue."""
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
