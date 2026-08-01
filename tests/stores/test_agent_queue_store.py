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


def _secretary_key() -> AgentQueueKey:
    return AgentQueueKey(role="secretary", owner_user_id=_OWNER, scope_id=None)


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
    key = _secretary_key()
    item = store.enqueue(_uid("s1"), key, "notice")

    assert item.scope_id is None
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.scope_id is None
    assert queue.key == key


def test_next_dispatchable_orders_by_priority_then_arrival(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    key = _worker_key()
    store.enqueue(_uid("first"), key, "notice")
    store.enqueue(_uid("second"), key, "notice")
    store.enqueue(_uid("urgent"), key, "notice", priority=5)

    head = store.next_dispatchable_item(key, now=_NOW)
    assert head is not None
    assert head.id == _uid("urgent")


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


def test_failed_dispatch_halts_only_its_own_queue(store: SqlAlchemyAgentQueueStore) -> None:
    failing = _worker_key("slot-a")
    healthy = _worker_key("slot-b")
    store.enqueue(_uid("a"), failing, "notice")
    store.enqueue(_uid("b"), healthy, "notice")

    failed = store.fail_dispatch(_uid("a"), failing, error="no runner bound", now=_NOW)
    assert failed is not None
    assert failed.state == "dispatch_failed"
    assert failed.last_error == "no runner bound"

    halted = store.get_queue(failing)
    assert halted is not None
    assert halted.state == "halted"
    assert halted.last_error == "no runner bound"
    assert halted.inflight_item_id is None

    other = store.get_queue(healthy)
    assert other is not None
    assert other.state == "active"


def test_halted_queue_is_not_scanned_and_does_not_retry(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    key = _worker_key()
    store.enqueue(_uid("poison"), key, "notice")
    store.enqueue(_uid("next"), key, "notice")
    store.fail_dispatch(_uid("poison"), key, error="boom", now=_NOW)

    assert store.due_queues(now=_NOW) == []
    assert store.acquire_lease(key, "replica-1", now=_NOW, ttl_s=30) is None


def test_resume_clears_the_halt_and_re_arms(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("poison"), key, "notice")
    store.enqueue(_uid("next"), key, "notice")
    store.fail_dispatch(_uid("poison"), key, error="boom", now=_NOW)

    resumed = store.set_queue_state(key, "active")
    assert resumed is not None
    assert resumed.state == "active"
    assert resumed.last_error is None
    # The failed item stays failed; the queue drains from the next one.
    assert [q.key for q in store.due_queues(now=_NOW)] == [key]
    head = store.next_dispatchable_item(key, now=_NOW)
    assert head is not None
    assert head.id == _uid("next")


def test_paused_queue_is_not_scanned(store: SqlAlchemyAgentQueueStore) -> None:
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
    key = _secretary_key()
    store.enqueue(_uid("a"), key, "notice", source_ids=[_uid("e1"), _uid("e2")])

    assert store.list_claimed_source_ids("secretary", _OWNER) == {_uid("e1"), _uid("e2")}

    store.mark_dispatched(_uid("a"), key, now=_NOW)
    # Still claimed while in flight — redelivering it would double-package.
    assert store.list_claimed_source_ids("secretary", _OWNER) == {_uid("e1"), _uid("e2")}

    store.complete_inflight(key, item_id=_uid("a"), now=_NOW + 1)
    assert store.list_claimed_source_ids("secretary", _OWNER) == set()


def test_watchdog_reclaims_a_stuck_in_flight_item(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.enqueue(_uid("b"), key, "notice")
    store.mark_dispatched(_uid("a"), key, now=_NOW)

    assert store.reclaim_stale_inflight(now=_NOW + 10, max_inflight_s=3600) == []

    reclaimed = store.reclaim_stale_inflight(now=_NOW + 7200, max_inflight_s=3600)
    assert [q.key for q in reclaimed] == [key]
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.inflight_item_id is None
    assert store.next_dispatchable_item(key, now=_NOW + 7200) is not None


def test_cancel_is_the_way_past_a_poisoned_head(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("poison"), key, "notice", priority=9)
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

    edited = store.update_item(_uid("a"), payload='{"v": 2}', priority=3)
    assert edited is not None
    assert edited.payload == '{"v": 2}'
    assert edited.priority == 3


def test_queue_depth_counts_only_waiting_items(store: SqlAlchemyAgentQueueStore) -> None:
    key = _worker_key()
    store.enqueue(_uid("a"), key, "notice")
    store.enqueue(_uid("b"), key, "notice")
    assert store.queue_depth(key) == 2

    store.mark_dispatched(_uid("a"), key, now=_NOW)
    assert store.queue_depth(key) == 1


def test_list_queues_filters_by_role_and_state(store: SqlAlchemyAgentQueueStore) -> None:
    worker = _worker_key()
    secretary = _secretary_key()
    store.enqueue(_uid("a"), worker, "notice")
    store.enqueue(_uid("b"), secretary, "notice")
    store.set_queue_state(worker, "paused")

    assert [q.key for q in store.list_queues(role="worker")] == [worker]
    assert [q.key for q in store.list_queues(state="paused")] == [worker]
    assert len(store.list_queues(owner_user_id=_OWNER)) == 2
