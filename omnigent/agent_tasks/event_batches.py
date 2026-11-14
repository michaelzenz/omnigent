"""Group GlobalHub task events into the batches a role session receives."""

from __future__ import annotations

from collections.abc import Sequence

from omnigent.entities import TaskEvent

HOST_TAG_TYPE = "host"
BATCH_MAX_EVENTS = 10
# A batch under the maximum waits this long for more events to join it.
PARTIAL_BATCH_MIN_AGE_S = 30
TAG_SIMILARITY_THRESHOLD = 0.8


def event_host(event: TaskEvent) -> str | None:
    """Return the host an event came from, by its ``host`` tag."""
    return next((tag.tag for tag in event.tags if tag.tag_type == HOST_TAG_TYPE), None)


def is_session_event(event: TaskEvent) -> bool:
    """Whether the event reports on a session, e.g. ``session.turn.finished``."""
    return event.event_type.startswith("session.")


def cluster_by_tags(
    events: Sequence[TaskEvent], *, threshold: float = TAG_SIMILARITY_THRESHOLD
) -> list[list[TaskEvent]]:
    """
    Cluster events by tag overlap, oldest first.

    Each cluster's leader is its oldest event. An event joins the first cluster
    whose leader's tags overlap its own by at least ``threshold`` (shared tags
    over the smaller tag count); untagged events share one last cluster.
    """
    clusters: list[list[TaskEvent]] = []
    leaders: list[set[tuple[str, str]]] = []
    untagged: list[TaskEvent] = []
    for event in _oldest_first(events):
        tags = {(tag.tag_type, tag.tag) for tag in event.tags}
        if not tags:
            untagged.append(event)
            continue
        for cluster, leader in zip(clusters, leaders, strict=True):
            if len(tags & leader) / min(len(tags), len(leader)) >= threshold:
                cluster.append(event)
                break
        else:
            clusters.append([event])
            leaders.append(tags)
    if untagged:
        clusters.append(untagged)
    return clusters


def broker_batches(
    events: Sequence[TaskEvent], *, now: int, broker_idle: bool
) -> list[list[TaskEvent]]:
    """
    Return the batches of one user's waiting events to queue for the broker now.

    Session events go out at once, one batch per session. Other events from one
    host fill one batch, tag cluster by cluster, oldest first, up to
    :data:`BATCH_MAX_EVENTS`. A full batch goes out at once; a smaller one only
    while the broker is idle and its oldest event is older than
    :data:`PARTIAL_BATCH_MIN_AGE_S`.
    """
    by_session: dict[str, list[TaskEvent]] = {}
    by_host: dict[str | None, list[TaskEvent]] = {}
    for event in _oldest_first(events):
        if is_session_event(event):
            key = event.source_session_id or event.source_key or event.id
            by_session.setdefault(key, []).append(event)
        else:
            by_host.setdefault(event_host(event), []).append(event)
    batches = list(by_session.values())
    for host_events in by_host.values():
        batch: list[TaskEvent] = []
        clusters = sorted(cluster_by_tags(host_events), key=lambda cluster: cluster[0].created_at)
        for cluster in clusters:
            batch.extend(cluster[: BATCH_MAX_EVENTS - len(batch)])
        oldest = min(event.created_at for event in batch)
        if len(batch) >= BATCH_MAX_EVENTS or (
            broker_idle and now - oldest > PARTIAL_BATCH_MIN_AGE_S
        ):
            batches.append(batch)
    return batches


def _oldest_first(events: Sequence[TaskEvent]) -> list[TaskEvent]:
    return sorted(events, key=lambda event: (event.created_at, event.id))
