"""Task discovery for event routing — search active and pending tasks."""

from __future__ import annotations

from typing import Any

from omnigent.agent_tasks.constants import AUTO_ROUTE_MAX_CANDIDATES
from omnigent.agent_tasks.scoring import rank_tasks_for_event_tags
from omnigent.entities import Task, TaskEvent, TaskEventTag, TaskTag
from omnigent.stores.agent_task.tags import merge_event_tags
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

_ROUTABLE_TASK_STATES = frozenset({"active", "pending", "idle"})
_LIVE_TASK_STATES = frozenset({"active", "idle"})


def routable_tasks(task_store: TaskStore) -> list[Task]:
    """Return active, idle, and pending tasks eligible for event routing."""
    tasks: list[Task] = []
    for state in sorted(_ROUTABLE_TASK_STATES):
        tasks.extend(task_store.list(state=state))
    return tasks


def live_tasks(task_store: TaskStore) -> list[Task]:
    """Return active and idle tasks that can receive routed events."""
    tasks: list[Task] = []
    for state in sorted(_LIVE_TASK_STATES):
        tasks.extend(task_store.list(state=state))
    return tasks


def rank_tasks_for_events(
    *,
    events: list[TaskEvent],
    tasks: list[Task],
    task_store: TaskStore,
    limit: int = AUTO_ROUTE_MAX_CANDIDATES,
) -> list[tuple[Task, float]]:
    """Score tasks against one or more events using merged ingress tags."""
    if not events:
        return []
    merged_tags = merge_event_tags(events)
    return rank_tasks_for_event_tags(
        event_tags=merged_tags,
        tasks=tasks,
        task_store=task_store,
        limit=limit,
    )


def ranked_task_payload(ranked: list[tuple[Task, float]]) -> list[dict[str, Any]]:
    """Serialize ranked tasks for API responses."""
    return [
        {
            "task_id": task.id,
            "title": task.title,
            "state": task.state,
            "score": round(score, 4),
        }
        for task, score in ranked
    ]


def collect_event_tags(
    event_ids: list[str],
    *,
    task_event_store: TaskEventStore,
) -> list[TaskEventTag]:
    """Merge tags from multiple events, last write wins per tag_type."""
    events = load_events(event_ids, task_event_store=task_event_store)
    return merge_event_tags(events)


def task_tags_from_event_tags(task_id: str, event_tags: list[TaskEventTag]) -> list[TaskTag]:
    """Convert event tags into task tags for a new managed task."""
    seen: set[tuple[str, str]] = set()
    tags: list[TaskTag] = []
    for event_tag in event_tags:
        key = (event_tag.tag_type, event_tag.tag)
        if key in seen:
            continue
        seen.add(key)
        tags.append(TaskTag(task_id=task_id, tag_type=event_tag.tag_type, tag=event_tag.tag))
    return tags


def internal_note_from_event_tags(event_tags: list[TaskEventTag]) -> str | None:
    """Derive a task internal_note hint from event tags."""
    for tag in event_tags:
        if tag.tag_type == "repo":
            return f"repo:{tag.tag}"
    return None


def load_events(
    event_ids: list[str],
    *,
    task_event_store: TaskEventStore,
) -> list[TaskEvent]:
    """Load task events by id, raising when any are missing."""
    from omnigent.errors import ErrorCode, OmnigentError

    events: list[TaskEvent] = []
    for event_id in event_ids:
        event = task_event_store.get_event(event_id)
        if event is None:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        events.append(event)
    return events
