"""Tag-overlap confidence scoring for task-event routing."""

from __future__ import annotations

from omnigent.agent_tasks.constants import (
    AUTO_ROUTE_MAX_CANDIDATES,
    AUTO_ROUTE_MIN_CONFIDENCE,
    AUTO_ROUTE_MIN_MARGIN,
)
from omnigent.entities import Task, EventTag, TaskTag
from omnigent.stores.agent_task.tags import tag_pair, task_tag_pairs
from omnigent.stores.task_store import TaskStore


def score_task_for_event_tags(
    *,
    event_tags: list[EventTag],
    task_tags: list[TaskTag],
) -> float:
    """Return the fraction of event tags matched on the task."""
    if not event_tags:
        return 0.0
    event_pairs = {tag_pair(tag.tag_type, tag.tag) for tag in event_tags}
    overlap = len(event_pairs & task_tag_pairs(task_tags))
    return overlap / len(event_pairs)


def candidate_task_ids_for_event_tags(
    event_tags: list[EventTag],
    *,
    task_store: TaskStore,
) -> set[str]:
    """Return task ids that share at least one tag with the event."""
    candidate_ids: set[str] = set()
    for tag in event_tags:
        candidate_ids.update(
            task_store.list_task_ids_by_tag(tag.tag_type, tag.tag),
        )
    return candidate_ids


def rank_tasks_for_event_tags(
    *,
    event_tags: list[EventTag],
    tasks: list[Task],
    task_store: TaskStore,
    limit: int = AUTO_ROUTE_MAX_CANDIDATES,
) -> list[tuple[Task, float]]:
    """Return tasks sorted by descending tag-overlap score."""
    if not event_tags:
        return []
    scored: list[tuple[Task, float]] = []
    for task in tasks:
        task_tags = task_store.get_tags(task.id)
        score = score_task_for_event_tags(event_tags=event_tags, task_tags=task_tags)
        if score <= 0:
            continue
        scored.append((task, score))
    scored.sort(key=lambda row: (-row[1], row[0].id))
    return scored[:limit]


def pick_auto_route(
    ranked: list[tuple[Task, float]],
    *,
    min_confidence: float = AUTO_ROUTE_MIN_CONFIDENCE,
    min_margin: float = AUTO_ROUTE_MIN_MARGIN,
) -> Task | None:
    """Return the task to auto-route, or ``None`` when routing should stall."""
    if not ranked:
        return None
    top_task, top_score = ranked[0]
    if top_score < min_confidence:
        return None
    if len(ranked) == 1:
        return top_task
    second_score = ranked[1][1]
    if top_score - second_score < min_margin:
        return None
    return top_task
