"""Broker ambiguous inbox — cluster and rank stalled task events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.task_match import rank_tasks_for_events, routable_tasks
from omnigent.entities import Task, TaskEvent
from omnigent.stores.agent_task.tags import tag_fingerprint, tag_pair, tags_to_payload
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
    clusters.extend(AmbiguousEventCluster(tags=[], events=[event]) for event in singletons)
    return clusters


def _tag_set(tags: list) -> set[tuple[str, str]]:
    return {tag_pair(t.tag_type, t.tag) for t in (tags or [])}


def _tag_overlap(a: set[tuple[str, str]], b: set[tuple[str, str]]) -> float:
    """Overlap coefficient: |A ∩ B| / min(|A|, |B|). 0 if either set is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def cluster_events_by_similarity(
    events: list[TaskEvent],
    *,
    threshold: float,
) -> list[AmbiguousEventCluster]:
    """Leader-based clustering by tag overlap, seeded oldest-first.

    Each cluster's leader is its earliest event; an event joins the first
    cluster whose leader shares >= ``threshold`` tag overlap (overlap
    coefficient: |A ∩ B| / min(|A|, |B|)). Tagless events all fall into one
    "untagged" bucket so they don't fan out into singleton clusters. Members
    are kept in oldest-first order.
    """
    if not events:
        return []
    ordered = sorted(events, key=lambda e: (e.created_at, e.id))
    tagless: list[TaskEvent] = []
    clusters: list[AmbiguousEventCluster] = []
    leaders: list[set[tuple[str, str]]] = []
    for event in ordered:
        if not event.tags:
            tagless.append(event)
            continue
        e_set = _tag_set(event.tags)
        for idx, leader_set in enumerate(leaders):
            if _tag_overlap(e_set, leader_set) >= threshold:
                clusters[idx].events.append(event)
                break
        else:
            clusters.append(
                AmbiguousEventCluster(tags=tags_to_payload(event.tags), events=[event])
            )
            leaders.append(e_set)
    if tagless:
        clusters.append(AmbiguousEventCluster(tags=[], events=tagless))
    return clusters


def event_summary(event: TaskEvent) -> dict[str, Any]:
    """Serialize a task event for broker inbox responses."""
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


def event_notice_entry(event: TaskEvent) -> dict[str, Any]:
    """Serialize a task event for the broker notice payload (includes body)."""
    entry = event_summary(event)
    entry["payload"] = event.payload
    return entry


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
    """Return ambiguous events and suggested clusters for broker reconcile."""
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
