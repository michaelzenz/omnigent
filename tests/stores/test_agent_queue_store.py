"""Tests for the agent-queue store."""

from __future__ import annotations

import uuid

import pytest

from omnigent.entities import AgentQueueKey
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore

_OWNER = "user-1"
_NOW = 1_800_000_000


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _worker_key(scope: str = "slot-a") -> AgentQueueKey:
    return AgentQueueKey(role="worker", owner_user_id=_OWNER, scope_id=_uid(scope))


def _broker_key() -> AgentQueueKey:
    return AgentQueueKey(role="broker", owner_user_id=_OWNER, scope_id=None)


@pytest.fixture
def store(db_uri: str) -> SqlAlchemyAgentQueueStore:
    return SqlAlchemyAgentQueueStore(db_uri)


def test_enqueue_creates_the_queue_row(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    item = store.enqueue(_uid("i1"), key, "item.dispatch", source_ids=[_uid("src")])

    assert item.state == "queued"
    assert item.source_ids == [_uid("src")]
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.state == "active"
    assert queue.inflight_item_id is None


def test_unscoped_queue_round_trips_as_none(store: SqlAlchemyAgentQueueStore) -> None:
    """A per-user role has no scope; the empty-string column form must not leak."""
    key = _broker_key()
    item = store.enqueue(_uid("s1"), key, "notice")

    assert item.scope_id is None
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.scope_id is None
    assert queue.key == key


def test_next_dispatchable_is_strictly_insert_order(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """Nothing jumps the queue — events and task items run in the order they arrived."""
    key = _worker_key()
    store.enqueue(_uid("first"), key, "notice")
    store.enqueue(_uid("second"), key, "notice")
    store.enqueue(_uid("third"), key, "notice")

    head = store.next_dispatchable_item(key, now=_NOW)
    assert head is not None
    assert head.id == _uid("first")


def test_same_second_items_keep_arrival_order(store: SqlAlchemyAgentQueueStore) -> None:
    """created_at is second-granularity, so ordering must not rely on it."""
    key = _worker_key()
    # Ids chosen so that sorting by id would reverse the insertion order.
    ordered = sorted((_uid("x"), _uid("y")), reverse=True)
    for item_id in ordered:
        store.enqueue(item_id, key, "notice")

    assert [item.id for item in store.list_items(key)] == ordered


def test_next_dispatchable_skips_snoozed_items(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("later"), key, "notice", not_before=_NOW + 60)

    assert store.next_dispatchable_item(key, now=_NOW) is None
    assert store.next_dispatchable_item(key, now=_NOW + 60) is not None


def test_only_one_item_is_in_flight_at_a_time(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.enqueue(_uid("b"), key, "notice")

    assert store.mark_dispatched(_uid("a"), key, now=_NOW) is not None
    # The second item is still the queue head, but the slot is taken.
    assert store.mark_dispatched(_uid("b"), key, now=_NOW) is None

    store.complete_inflight(key, item_id=_uid("a"), now=_NOW + 10)
    assert store.mark_dispatched(_uid("b"), key, now=_NOW + 10) is not None


def test_dispatching_an_item_twice_is_rejected(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.mark_dispatched(_uid("a"), key, now=_NOW)
    store.complete_inflight(key, item_id=_uid("a"), now=_NOW + 1)

    assert store.mark_dispatched(_uid("a"), key, now=_NOW + 2) is None


def test_completion_is_conditional_on_the_item(store: SqlAlchemyAgentQueueStore) -> None:
    """A late signal from a finished item must not clear a newer in-flight one."""
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.enqueue(_uid("b"), key, "notice")
    store.mark_dispatched(_uid("a"), key, now=_NOW)
    store.complete_inflight(key, item_id=_uid("a"), now=_NOW + 1)
    store.mark_dispatched(_uid("b"), key, now=_NOW + 2)

    assert store.complete_inflight(key, item_id=_uid("a"), now=_NOW + 3) is None
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.inflight_item_id == _uid("b")


def test_completed_item_is_done(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.mark_dispatched(_uid("a"), key, now=_NOW)

    completed = store.complete_inflight(key, item_id=_uid("a"), now=_NOW + 5)
    assert completed is not None
    assert completed.state == "done"
    assert completed.completed_at == _NOW + 5


def test_failed_dispatch_requeues_and_isolates_queues(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """A failed dispatch re-queues with backoff; only that queue is touched.

    Dispatch failures never park work (the queue stays active so the
    dispatcher keeps trying — a runner restart heals it on its own); the
    failure must also not leak into unrelated queues.
    """
    failing = _worker_key("slot-a")
    healthy = _worker_key("slot-b")
    store.enqueue(_uid("a"), failing, "notice")
    store.enqueue(_uid("b"), healthy, "notice")

    failed = store.fail_dispatch(_uid("a"), failing, error="no runner bound", now=_NOW)
    assert failed is not None
    # Re-queued behind the non-retryable backoff window, not parked.
    assert failed.state == "queued"
    assert failed.last_error == "no runner bound"
    assert failed.not_before == _NOW + 300

    queue = store.get_queue(failing)
    assert queue is not None
    assert queue.state == "active"
    assert queue.last_error == "no runner bound"
    assert queue.inflight_item_id is None

    other = store.get_queue(healthy)
    assert other is not None
    assert other.state == "active"
    assert other.last_error is None


def test_failed_item_retries_after_backoff(store: SqlAlchemyAgentQueueStore) -> None:
    """A dispatch-failed item sits out its backoff, then retries.

    The queue keeps draining meanwhile: the failed item is gated by its own
    ``not_before`` (the dispatcher picks ``next`` first), then re-enters the
    scan once the backoff window passes.
    """
    key = _worker_key()
    store.enqueue(_uid("poison"), key, "notice")
    store.fail_dispatch(_uid("poison"), key, error="boom", now=_NOW)

    # The failed item is gated by its backoff window, not dropped: the
    # dispatcher pick returns None until the window passes, then the item
    # comes back.
    assert store.next_dispatchable_item(key, now=_NOW) is None
    assert store.next_dispatchable_item(key, now=_NOW + 299) is None
    retried = store.next_dispatchable_item(key, now=_NOW + 300)
    assert retried is not None
    assert retried.id == _uid("poison")


def test_cancel_removes_the_requeued_failed_item(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """Cancelling a dispatch-failed item removes it; the queue keeps running.

    There is no halt to clear anymore — the item re-queued with backoff, so
    cancel just deletes it and the slot drains from the next item.
    """
    key = _worker_key()
    store.enqueue(_uid("poison"), key, "notice")
    store.enqueue(_uid("next"), key, "notice")
    store.fail_dispatch(_uid("poison"), key, error="boom", now=_NOW)

    cancelled = store.cancel_item(_uid("poison"), now=_NOW)
    assert cancelled is not None
    assert cancelled.state == "cancelled"

    queue = store.get_queue(key)
    assert queue is not None
    assert queue.state == "active"
    # The failure note persists until the next event rewrites it (intended:
    # cancel does not rewrite queue history).
    assert queue.last_error == "boom"
    # The dispatcher pick is not blocked, and the queue-level scan gate set
    # by the failure opens once its backoff passes.
    head = store.next_dispatchable_item(key, now=_NOW)
    assert head is not None
    assert head.id == _uid("next")
    assert [q.key for q in store.due_queues(now=_NOW + 300)] == [key]


def test_cancel_is_idempotent_on_already_cancelled(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    key = _worker_key()
    store.enqueue(_uid("idem"), key, "notice")
    first = store.cancel_item(_uid("idem"), now=_NOW)
    assert first is not None
    assert first.state == "cancelled"

    second = store.cancel_item(_uid("idem"), now=_NOW)
    assert second is not None
    assert second.state == "cancelled"
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.set_queue_state(key, "paused")

    assert store.due_queues(now=_NOW) == []


def test_due_queues_skips_queues_with_work_in_flight(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.enqueue(_uid("b"), key, "notice")
    store.mark_dispatched(_uid("a"), key, now=_NOW)

    assert store.due_queues(now=_NOW) == []


def test_due_queues_skips_empty_and_not_yet_due_queues(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    key = _worker_key()
    store.enqueue(_uid("later"), key, "notice", not_before=_NOW + 60)
    assert store.due_queues(now=_NOW) == []
    assert [q.key for q in store.due_queues(now=_NOW + 60)] == [key]


def test_lease_is_exclusive_until_it_expires(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")

    assert store.acquire_lease(key, "replica-1", now=_NOW, ttl_s=30) is not None
    assert store.acquire_lease(key, "replica-2", now=_NOW, ttl_s=30) is None
    # A dispatcher that died mid-item must not wedge the queue forever.
    assert store.acquire_lease(key, "replica-2", now=_NOW + 31, ttl_s=30) is not None


def test_lease_renewal_only_works_for_the_holder(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.acquire_lease(key, "replica-1", now=_NOW, ttl_s=30)

    assert store.renew_lease(key, "replica-1", now=_NOW + 10, ttl_s=30) is True
    assert store.renew_lease(key, "replica-2", now=_NOW + 10, ttl_s=30) is False
    # The renewal pushed the expiry out, so the stale-steal window moved too.
    assert store.acquire_lease(key, "replica-2", now=_NOW + 31, ttl_s=30) is None


def test_released_lease_is_available_again(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.acquire_lease(key, "replica-1", now=_NOW, ttl_s=30)
    store.release_lease(key, "replica-1")

    assert store.acquire_lease(key, "replica-2", now=_NOW, ttl_s=30) is not None


def test_release_can_defer_the_next_scan(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.acquire_lease(key, "replica-1", now=_NOW, ttl_s=30)
    store.release_lease(key, "replica-1", next_due_at=_NOW + 3)

    assert store.due_queues(now=_NOW) == []
    assert [q.key for q in store.due_queues(now=_NOW + 3)] == [key]


def test_claimed_source_ids_cover_open_items_only(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    key = _broker_key()
    store.enqueue(_uid("a"), key, "notice", source_ids=[_uid("e1"), _uid("e2")])

    assert store.list_claimed_source_ids("broker", _OWNER) == {_uid("e1"), _uid("e2")}

    store.mark_dispatched(_uid("a"), key, now=_NOW)
    # Still claimed while in flight — redelivering it would double-package.
    assert store.list_claimed_source_ids("broker", _OWNER) == {_uid("e1"), _uid("e2")}

    store.complete_inflight(key, item_id=_uid("a"), now=_NOW + 1)
    assert store.list_claimed_source_ids("broker", _OWNER) == set()


def test_watchdog_requeues_a_stuck_in_flight_item(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """An agent that went away mid-item leaves the work retryable, not done.

    The in-flight slot is freed and the item re-queues behind a backoff with
    the failure recorded — never marked finished, never parked.
    """
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.enqueue(_uid("b"), key, "notice")
    store.mark_dispatched(_uid("a"), key, now=_NOW)

    assert store.reclaim_stale_inflight(now=_NOW + 10, max_inflight_s=3600) == []

    reclaimed = store.reclaim_stale_inflight(now=_NOW + 7200, max_inflight_s=3600)
    assert [item.id for item in reclaimed] == [_uid("a")]
    assert reclaimed[0].state == "queued"
    assert reclaimed[0].last_error == "agent went away while the item was in flight"
    assert reclaimed[0].not_before == _NOW + 7200 + 30
    assert reclaimed[0].retry_count == 1
    assert reclaimed[0].completed_at is None

    queue = store.get_queue(key)
    assert queue is not None
    assert queue.inflight_item_id is None
    assert queue.state == "active"


def test_parked_items_keep_their_source_claims(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """A parked item is retryable, so re-packaging its sources would duplicate it."""
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice", source_ids=["event-1"])
    store.mark_dispatched(_uid("a"), key, now=_NOW)
    store.reclaim_stale_inflight(now=_NOW + 7200, max_inflight_s=3600)

    assert store.list_claimed_source_ids(key.role, _OWNER, scope_id=key.scope_id) == {"event-1"}

    store.cancel_item(_uid("a"), now=_NOW + 7200)
    assert store.list_claimed_source_ids(key.role, _OWNER, scope_id=key.scope_id) == set()


def test_cancel_is_the_way_past_a_poisoned_head(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("poison"), key, "notice")
    store.enqueue(_uid("good"), key, "notice")

    cancelled = store.cancel_item(_uid("poison"), now=_NOW)
    assert cancelled is not None
    assert cancelled.state == "cancelled"
    head = store.next_dispatchable_item(key, now=_NOW)
    assert head is not None
    assert head.id == _uid("good")


def test_dispatched_items_cannot_be_edited_or_cancelled(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """A payload must not change out from under a running agent."""
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice", payload='{"v": 1}')
    store.mark_dispatched(_uid("a"), key, now=_NOW)

    assert store.update_item(_uid("a"), payload='{"v": 2}') is None
    assert store.cancel_item(_uid("a"), now=_NOW) is None


def test_queued_item_payload_can_be_edited(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice", payload='{"v": 1}')

    edited = store.update_item(_uid("a"), payload='{"v": 2}')
    assert edited is not None
    assert edited.payload == '{"v": 2}'


def test_queue_depth_counts_only_waiting_items(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.enqueue(_uid("b"), key, "notice")
    assert store.queue_depth(key) == 2

    store.mark_dispatched(_uid("a"), key, now=_NOW)
    assert store.queue_depth(key) == 1


def test_list_queues_filters_by_role_and_state(store: SqlAlchemyAgentQueueStore) -> None:
    worker = _worker_key()
    broker = _broker_key()
    store.enqueue(_uid("a"), worker, "notice")
    store.enqueue(_uid("b"), broker, "notice")
    store.set_queue_state(worker, "paused")

    assert [q.key for q in store.list_queues(role="worker")] == [worker]
    assert [q.key for q in store.list_queues(state="paused")] == [worker]
    assert len(store.list_queues(owner_user_id=_OWNER)) == 2


def test_dispatch_stoplist_roundtrip(store: SqlAlchemyAgentQueueStore) -> None:
    """The stoplist is empty by default and idempotent on both directions."""
    assert store.get_dispatch_stoplist() == frozenset()

    store.set_role_dispatch_stopped("broker", True)
    assert store.get_dispatch_stoplist() == frozenset({"broker"})
    store.set_role_dispatch_stopped("broker", True)  # re-add is a no-op
    assert store.get_dispatch_stoplist() == frozenset({"broker"})

    store.set_role_dispatch_stopped("manager", True)
    assert store.get_dispatch_stoplist() == frozenset({"broker", "manager"})

    store.set_role_dispatch_stopped("broker", False)
    assert store.get_dispatch_stoplist() == frozenset({"manager"})
    store.set_role_dispatch_stopped("broker", False)  # removing an absent role is a no-op
    assert store.get_dispatch_stoplist() == frozenset({"manager"})
