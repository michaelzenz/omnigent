"""Secretary ambiguous inbox — cluster and rank stalled task events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.task_match import rank_tasks_for_events, routable_tasks
from omnigent.entities import Task, TaskEvent, TaskEventTag
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore


@dataclass(frozen=True)
class AmbiguousEventCluster:
    """Ambiguous events bundled by shared tags."""

    tags: list[dict[str, str]]
    events: list[TaskEvent]


def _tag_fingerprint(tags: list[TaskEventTag]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((tag.tag_type, tag.tag) for tag in tags))


def _tags_to_payload(tags: list[TaskEventTag]) -> list[dict[str, str]]:
    return [{"tag_type": tag.tag_type, "tag": tag.tag} for tag in tags]


def cluster_ambiguous_events(
    events: list[TaskEvent],
    *,
    tags_by_event_id: dict[str, list[TaskEventTag]],
) -> list[AmbiguousEventCluster]:
    """Group ambiguous events that share the same event tags."""
    buckets: dict[tuple[tuple[str, str], ...], list[TaskEvent]] = {}
    singletons: list[TaskEvent] = []
    for event in events:
        tags = tags_by_event_id.get(event.id, [])
        if not tags:
            singletons.append(event)
            continue
        fingerprint = _tag_fingerprint(tags)
        buckets.setdefault(fingerprint, []).append(event)

    clusters = [
        AmbiguousEventCluster(
            tags=_tags_to_payload(tags_by_event_id[rows[0].id]),
            events=rows,
        )
        for rows in buckets.values()
    ]
    clusters.extend(
        AmbiguousEventCluster(tags=[], events=[event]) for event in singletons
    )
    return clusters


def event_summary(event: TaskEvent) -> dict[str, Any]:
    """Serialize a task event for secretary inbox responses."""
    return {
        "id": event.id,
        "event_type": event.event_type,
        "title": event.title,
        "summary": event.summary,
        "state": event.state,
        "source": event.source,
        "source_key": event.source_key,
        "created_at": event.created_at,
    }


def _candidate_payload(ranked: list[tuple[Task, float]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": task.id,
            "title": task.title,
            "state": task.state,
            "score": round(score, 4),
        }
        for task, score in ranked[:5]
    ]


def build_ambiguous_inbox(
    *,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
    task_store: TaskStore,
) -> dict[str, Any]:
    """Return ambiguous events and suggested clusters for secretary reconcile."""
    ambiguous_events = []
    for event in task_event_store.list_events(state="awaiting_grouping"):
        if task_item_store.get_item_for_event(event.id) is not None:
            continue
        if task_item_store.get_fyi_cluster_for_event(event.id) is not None:
            continue
        ambiguous_events.append(event)

    tags_by_event_id: dict[str, list[TaskEventTag]] = {}
    for event in ambiguous_events:
        tags_by_event_id[event.id] = task_event_store.get_event_tags(event.id)

    clusters = cluster_ambiguous_events(ambiguous_events, tags_by_event_id=tags_by_event_id)
    searchable_tasks = routable_tasks(task_store)
    rendered_clusters: list[dict[str, Any]] = []
    for cluster in clusters:
        ranked = rank_tasks_for_events(events=cluster.events, tasks=searchable_tasks)
        candidate_payload = _candidate_payload(ranked)
        rendered_clusters.append(
            {
                "tags": cluster.tags,
                "events": [event_summary(event) for event in cluster.events],
                "suggested_candidates": candidate_payload,
            },
        )

    return {
        "object": "agent.task.ambiguous_inbox",
        "clusters": rendered_clusters,
        "unclustered_count": 0,
    }
