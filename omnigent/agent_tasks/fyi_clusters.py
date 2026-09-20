"""Broker FYI clusters for informational orphan task events."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from omnigent.agent_tasks.broker_inbox import event_summary
from omnigent.agent_tasks.constants import (
    AMBIGUOUS_EVENT_STATES,
    CLASSIFIED_FYI_EVENT_STATE,
    FYI_CLUSTER_OPEN_STATE,
)
from omnigent.db.utils import now_epoch
from omnigent.entities import FyiCluster
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore

FyiResolution = Literal["dismiss_fyi"]


def _generate_cluster_id() -> str:
    return uuid.uuid4().hex


def _claimable_fyi_events(
    event_ids: list[str],
    *,
    owner_user_id: str,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
) -> list[str]:
    claimed: list[str] = []
    for event_id in event_ids:
        if task_item_store.get_item_for_event(event_id) is not None:
            continue
        if task_item_store.get_fyi_cluster_for_event(event_id) is not None:
            continue
        event = task_event_store.get_event(event_id)
        if event is None:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        if (event.owner_user_id or "__anonymous__") != owner_user_id:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        is_owned_manager_route = event.state == "routed" and event.manager_id is not None
        if event.state not in AMBIGUOUS_EVENT_STATES and not is_owned_manager_route:
            continue
        claimed.append(event_id)
    return claimed


def create_fyi_cluster(
    *,
    owner_user_id: str,
    headline: str,
    event_ids: list[str],
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    cluster_id: str | None = None,
    rationale: str | None = None,
) -> FyiCluster | None:
    """Create or extend a broker FYI cluster over orphan events."""
    claimed_ids = _claimable_fyi_events(
        event_ids,
        owner_user_id=owner_user_id,
        task_event_store=task_event_store,
        task_item_store=task_item_store,
    )
    if not claimed_ids:
        return None

    if cluster_id is not None:
        cluster = task_item_store.get_fyi_cluster(cluster_id)
        if cluster is None:
            raise OmnigentError("FYI cluster not found", code=ErrorCode.NOT_FOUND)
        if cluster.owner_user_id != owner_user_id:
            raise OmnigentError("FYI cluster not found", code=ErrorCode.NOT_FOUND)
        if cluster.state != FYI_CLUSTER_OPEN_STATE:
            raise OmnigentError(
                f"Cannot extend FYI cluster in state {cluster.state!r}",
                code=ErrorCode.CONFLICT,
            )
        if headline != cluster.headline or rationale != cluster.rationale:
            updated = task_item_store.update_fyi_cluster(
                cluster.id,
                headline=headline,
                rationale=rationale,
            )
            assert updated is not None
            cluster = updated
    else:
        cluster = task_item_store.create_fyi_cluster(
            _generate_cluster_id(),
            owner_user_id,
            headline,
            rationale=rationale,
        )

    for event_id in claimed_ids:
        task_item_store.link_fyi_cluster_event(cluster.id, event_id)
        task_event_store.update_event(event_id, state=CLASSIFIED_FYI_EVENT_STATE)
    return cluster


def list_fyi_board_cards(
    *,
    owner_user_id: str | None,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
) -> list[dict[str, Any]]:
    """Build board FYI cards for open broker clusters."""
    clusters = task_item_store.list_fyi_clusters(state=FYI_CLUSTER_OPEN_STATE)
    cards: list[dict[str, Any]] = []
    for cluster in clusters:
        if owner_user_id is not None and cluster.owner_user_id not in {
            owner_user_id,
            "__anonymous__",
        }:
            continue
        event_ids = task_item_store.list_fyi_cluster_event_ids(cluster.id)
        events = []
        for event_id in event_ids:
            event = task_event_store.get_event(event_id)
            if event is not None:
                events.append(event_summary(event))
        cards.append(
            {
                "id": cluster.id,
                "kind": "fyi_cluster",
                "state": "pending",
                "created_at": cluster.created_at,
                "resolved_at": None,
                "headline": cluster.headline,
                "rationale": cluster.rationale,
                "body": {
                    "events": events,
                },
            },
        )
    return cards


def dismiss_fyi_cluster(
    *,
    cluster: FyiCluster,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
) -> FyiCluster:
    """Dismiss an FYI cluster and mark its events dismissed."""
    if cluster.state != FYI_CLUSTER_OPEN_STATE:
        raise OmnigentError(
            f"Cannot resolve FYI cluster in state {cluster.state!r}",
            code=ErrorCode.CONFLICT,
        )

    for event_id in task_item_store.list_fyi_cluster_event_ids(cluster.id):
        task_event_store.update_event(event_id, state="dismissed", processed_at=now_epoch())
    updated = task_item_store.update_fyi_cluster(
        cluster.id,
        state="dismissed",
        resolved_at=now_epoch(),
    )
    assert updated is not None
    return updated
