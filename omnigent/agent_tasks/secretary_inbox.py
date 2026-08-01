"""Secretary ambiguous inbox — cluster and rank stalled task events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.task_match import rank_tasks_for_events, routable_tasks
from omnigent.entities import Task, TaskEvent
from omnigent.stores.agent_task.tags import tag_fingerprint, tags_to_payload
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore


@dataclass(frozen=True)
class AmbiguousEventCluster:
    """Ambiguous events bundled by shared tags."""

    tags: list[dict[str, str]]
    events: list[TaskEvent]


def cluster_ambiguous_events(events: list[TaskEvent]) -> list[AmbiguousEventCluster]:
    """Group ambiguous events that share the same ingress tags."""
    buckets: dict[tuple[tuple[str, str], ...], list[TaskEvent]] = {}
    singletons: list[TaskEvent] = []
    for event in events:
        tags = event.tags or []
        if not tags:
            singletons.append(event)
            continue
        fingerprint = tag_fingerprint(tags)
        buckets.setdefault(fingerprint, []).append(event)

    clusters = [
        AmbiguousEventCluster(
            tags=tags_to_payload(rows[0].tags or []),
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
        "state": event.state,
        "source": event.source,
        "source_key": event.source_key,
        "created_at": event.created_at,
        "tags": tags_to_payload(event.tags or []),
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

    clusters = cluster_ambiguous_events(ambiguous_events)
    searchable_tasks = routable_tasks(task_store)
    rendered_clusters: list[dict[str, Any]] = []
    for cluster in clusters:
        ranked = rank_tasks_for_events(
            events=cluster.events,
            tasks=searchable_tasks,
            task_store=task_store,
        )
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
