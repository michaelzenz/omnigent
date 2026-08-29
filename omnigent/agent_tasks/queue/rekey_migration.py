"""One-time migration: re-key manager queues from task scope to manager scope.

Manager queue keys moved from ``manager/<owner>/<task_id>`` to
``manager/<owner>/<manager_conversation_id>``. Every open manager-role queue
item still claims its source events under an old key, so the migration cancels
those derived items — durable events are the source of truth. Cancelled items
drop their claims, and their still-``routed`` source events are re-packaged by
the manager packager under the new keys on the next poll.

In-flight (``dispatched``) items are left alone: their queue rows carry the
manager conversation id, so the status feed completes them naturally on the
next idle edge.
"""

from __future__ import annotations

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_ROLE
from omnigent.db.utils import now_epoch
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.task_event_store import TaskEventStore

_CANCELABLE_STATES = frozenset({"queued", "dispatch_failed", "interrupted"})


def rekey_manager_queues(
    *,
    agent_queue_store: AgentQueueStore,
    task_event_store: TaskEventStore,
    now: int | None = None,
) -> dict[str, int]:
    """Cancel open manager queue items so their events re-package.

    Cancelling an item drops its source-id claim; events still ``routed``
    (not yet reconciled by a manager) are picked up again by the packager.
    Events already ``reconciled`` are done work and are never resurrected.

    :returns: ``{"items_canceled", "events_requeued", "items_in_flight"}``.
    """
    now = now if now is not None else now_epoch()
    items_canceled = 0
    events_requeued = 0
    items_in_flight = 0
    for item in agent_queue_store.list_open_items_for_role(TASK_MANAGER_ROLE):
        if item.state not in _CANCELABLE_STATES:
            items_in_flight += 1
            continue
        if agent_queue_store.cancel_item(item.id, now=now) is None:
            continue
        items_canceled += 1
        for event_id in item.source_ids or []:
            event = task_event_store.get_event(event_id)
            if event is not None and event.state == "routed":
                events_requeued += 1
    return {
        "items_canceled": items_canceled,
        "events_requeued": events_requeued,
        "items_in_flight": items_in_flight,
    }
