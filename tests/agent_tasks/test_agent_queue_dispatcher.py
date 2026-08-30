"""Tests for the agent-queue dispatcher."""

from __future__ import annotations

import logging
import uuid

import pytest

from omnigent.agent_tasks.queue import dispatcher as dispatcher_module
from omnigent.agent_tasks.queue.dispatcher import (
    AgentQueueDispatcher,
    DispatcherContext,
    DispatchFailed,
    DispatchTarget,
    RoleDispatchHandler,
    StatusReader,
)
from omnigent.db.utils import now_epoch
from omnigent.entities import AgentQueueItem, AgentQueueKey
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore

_OWNER = "user-1"
_SESSION = "11111111111111111111111111111111"


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _key(scope: str = "slot-a") -> AgentQueueKey:
    return AgentQueueKey(role="worker", owner_user_id=_OWNER, scope_id=_uid(scope))


class _FakeStatus(StatusReader):
    """Reports one status for every session."""

    def __init__(self, status: str | None = "idle") -> None:
        self.status = status

    async def status_for(self, session_id: str) -> str | None:
        return self.status


class _RecordingHandler(RoleDispatchHandler):
    """Records deliveries, optionally failing them."""

    def __init__(
        self,
        *,
        session_id: str | None = None,
        deliver_error: str | None = None,
        resolve_error: str | None = None,
    ) -> None:
        self.delivered: list[AgentQueueItem] = []
        self._session_id = session_id
        self._deliver_error = deliver_error
        self._resolve_error = resolve_error

    async def resolve_target(self, item: AgentQueueItem) -> DispatchTarget:
        if self._resolve_error is not None:
            raise DispatchFailed(self._resolve_error)
        return DispatchTarget(session_id=self._session_id)

    async def deliver(self, item: AgentQueueItem, target: DispatchTarget) -> None:
        if self._deliver_error is not None:
            raise DispatchFailed(self._deliver_error)
        self.delivered.append(item)


def _dispatcher(
    store: SqlAlchemyAgentQueueStore,
    handler: RoleDispatchHandler | None,
    *,
    status: str | None = "idle",
    grace_period_s: float = 0.0,
) -> AgentQueueDispatcher:
    return AgentQueueDispatcher(
        DispatcherContext(
            store=store,
            handlers={} if handler is None else {"worker": handler},
            read_status=_FakeStatus(status),
            grace_period_s=grace_period_s,
        )
    )


@pytest.fixture
def store(db_uri: str) -> SqlAlchemyAgentQueueStore:
    return SqlAlchemyAgentQueueStore(db_uri)


@pytest.mark.asyncio
async def test_dispatches_one_item(store: SqlAlchemyAgentQueueStore) -> None:
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    handler = _RecordingHandler()

    assert await _dispatcher(store, handler).run_once() == 1
    assert [item.id for item in handler.delivered] == [_uid("a")]


@pytest.mark.asyncio
async def test_only_one_item_goes_out_until_completion(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """The serial guarantee: the queue holds until the in-flight item finishes."""
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    store.enqueue(_uid("b"), key, "item.dispatch")
    handler = _RecordingHandler()
    dispatcher = _dispatcher(store, handler)

    assert await dispatcher.run_once() == 1
    assert await dispatcher.run_once() == 0
    assert len(handler.delivered) == 1

    store.complete_inflight(key, item_id=_uid("a"), now=2_000)
    assert await dispatcher.run_once() == 1
    assert [item.id for item in handler.delivered] == [_uid("a"), _uid("b")]


@pytest.mark.asyncio
async def test_busy_session_blocks_dispatch(store: SqlAlchemyAgentQueueStore) -> None:
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    handler = _RecordingHandler(session_id=_SESSION)

    dispatcher = _dispatcher(store, handler, status="running")
    assert await dispatcher.run_once() == 0
    assert handler.delivered == []


@pytest.mark.asyncio
async def test_quiet_session_is_dispatched_to(store: SqlAlchemyAgentQueueStore) -> None:
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    handler = _RecordingHandler(session_id=_SESSION)

    assert await _dispatcher(store, handler, status="idle").run_once() == 1


@pytest.mark.asyncio
async def test_failed_session_requeues_for_retry(store: SqlAlchemyAgentQueueStore) -> None:
    """``failed`` is sticky for the gate, but the queue retries instead of dying."""
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    handler = _RecordingHandler(session_id=_SESSION)

    assert await _dispatcher(store, handler, status="failed").run_once() == 0
    assert handler.delivered == []
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.state == "active"
    item = store.get_item(_uid("a"))
    assert item is not None
    assert item.state == "queued"
    assert item.retry_count == 1
    assert item.last_error is not None


@pytest.mark.asyncio
async def test_delivery_failure_requeues_the_item(store: SqlAlchemyAgentQueueStore) -> None:
    """A transient delivery failure retries with backoff; the slot stays live."""
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    store.enqueue(_uid("b"), key, "item.dispatch")
    handler = _RecordingHandler(deliver_error="no runner bound")
    dispatcher = _dispatcher(store, handler)

    assert await dispatcher.run_once() == 0

    failed = store.get_item(_uid("a"))
    assert failed is not None
    assert failed.state == "queued"
    assert failed.retry_count == 1
    assert failed.last_error == "no runner bound"
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.state == "active"
    assert queue.inflight_item_id is None
    # 'a' is waiting out its backoff: the slot may send 'b' meanwhile, but 'a'
    # itself only becomes dispatchable again once not_before passes.
    head = store.next_dispatchable_item(key, now=now_epoch() + 5)
    assert head is not None
    assert head.id != _uid("a")
    retried = store.next_dispatchable_item(key, now=now_epoch() + 60)
    assert retried is not None
    assert retried.id == _uid("a")


@pytest.mark.asyncio
async def test_retry_succeeds_once_the_failure_clears(
    store: SqlAlchemyAgentQueueStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restart that brings the runner back heals the queue without a resume."""
    monkeypatch.setattr(dispatcher_module, "_BASE_BACKOFF_S", 0)
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    failing = _RecordingHandler(deliver_error="no runner bound")
    assert await _dispatcher(store, failing).run_once() == 0

    healthy = _RecordingHandler()
    assert await _dispatcher(store, healthy).run_once() == 1
    assert [item.id for item in healthy.delivered] == [_uid("a")]
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.state == "active"


@pytest.mark.asyncio
async def test_retry_backoff_is_capped(store: SqlAlchemyAgentQueueStore) -> None:
    """A long outage retries at most every _MAX_BACKOFF_S, never with runaway delays."""
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    # Push the retry count well past the point where 30s * 2^n would run away.
    for _ in range(12):
        assert store.mark_dispatched(_uid("a"), key, now=now_epoch()) is not None
        store.fail_dispatch(
            _uid("a"), key, error="boom", now=now_epoch(),
            retryable=True, max_retries=None, backoff_s=0,
        )

    handler = _RecordingHandler(deliver_error="still down")
    before = now_epoch()
    assert await _dispatcher(store, handler).run_once() == 0
    item = store.get_item(_uid("a"))
    assert item is not None
    assert item.retry_count == 13
    assert item.not_before is not None
    assert item.not_before - before <= dispatcher_module._MAX_BACKOFF_S + 5
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.state == "active"


@pytest.mark.asyncio
async def test_every_retry_attempt_is_logged(
    store: SqlAlchemyAgentQueueStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each retry going out says so in the log, with the prior error."""
    monkeypatch.setattr(dispatcher_module, "_BASE_BACKOFF_S", 0)
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    assert await _dispatcher(store, _RecordingHandler(deliver_error="down")).run_once() == 0

    with caplog.at_level(logging.INFO, logger="omnigent.agent_tasks.queue.dispatcher"):
        assert await _dispatcher(store, _RecordingHandler()).run_once() == 1
    assert any("retrying item" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_unresolvable_target_requeues_for_retry(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    handler = _RecordingHandler(resolve_error="worker slot is gone")

    assert await _dispatcher(store, handler).run_once() == 0
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.state == "active"
    assert queue.last_error == "worker slot is gone"
    item = store.get_item(_uid("a"))
    assert item is not None
    assert item.state == "queued"


@pytest.mark.asyncio
async def test_one_broken_queue_does_not_stop_the_others(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """Retries are per queue: a broken worker slot must not stop other agents."""
    broken = _key("slot-a")
    healthy = _key("slot-b")
    store.enqueue(_uid("a"), broken, "item.dispatch")
    store.enqueue(_uid("b"), healthy, "item.dispatch")

    class _SelectiveHandler(RoleDispatchHandler):
        def __init__(self) -> None:
            self.delivered: list[AgentQueueItem] = []

        async def resolve_target(self, item: AgentQueueItem) -> DispatchTarget:
            return DispatchTarget(session_id=None)

        async def deliver(self, item: AgentQueueItem, target: DispatchTarget) -> None:
            if item.id == _uid("a"):
                raise DispatchFailed("boom")
            self.delivered.append(item)

    handler = _SelectiveHandler()
    assert await _dispatcher(store, handler).run_once() == 1
    assert [item.id for item in handler.delivered] == [_uid("b")]

    broken_queue = store.get_queue(broken)
    assert broken_queue is not None
    # The broken slot keeps retrying its item on its own backoff clock.
    assert broken_queue.state == "active"
    broken_item = store.get_item(_uid("a"))
    assert broken_item is not None
    assert broken_item.state == "queued"
    assert broken_item.retry_count == 1
    healthy_queue = store.get_queue(healthy)
    assert healthy_queue is not None
    assert healthy_queue.state == "active"


@pytest.mark.asyncio
async def test_unexpected_delivery_error_does_not_wedge_the_queue(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """An unexpected exception must still clear the in-flight marker."""
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")

    class _ExplodingHandler(RoleDispatchHandler):
        async def resolve_target(self, item: AgentQueueItem) -> DispatchTarget:
            return DispatchTarget(session_id=None)

        async def deliver(self, item: AgentQueueItem, target: DispatchTarget) -> None:
            raise RuntimeError("kaboom")

    assert await _dispatcher(store, _ExplodingHandler()).run_once() == 0
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.inflight_item_id is None
    # The item is requeued for retry, not lost, and the slot stays live.
    assert queue.state == "active"
    item = store.get_item(_uid("a"))
    assert item is not None
    assert item.state == "queued"
    assert item.last_error is not None
    assert "kaboom" in item.last_error


@pytest.mark.asyncio
async def test_missing_handler_holds_the_item_without_halting(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """An unenabled role is a configuration gap, not a broken agent."""
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")

    assert await _dispatcher(store, None).run_once() == 0
    queue = store.get_queue(key)
    assert queue is not None
    assert queue.state == "active"
    item = store.get_item(_uid("a"))
    assert item is not None
    assert item.state == "queued"


@pytest.mark.asyncio
async def test_lease_is_released_after_each_pass(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    """A lease must not be held for the item's duration."""
    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    await _dispatcher(store, _RecordingHandler()).run_once()

    queue = store.get_queue(key)
    assert queue is not None
    assert queue.lease_owner is None
    # Still in flight, so the next item stays put — the marker, not the lease.
    assert queue.inflight_item_id == _uid("a")


@pytest.mark.asyncio
async def test_two_dispatchers_never_drain_one_queue(
    store: SqlAlchemyAgentQueueStore,
) -> None:
    import asyncio

    key = _key()
    store.enqueue(_uid("a"), key, "item.dispatch")
    store.enqueue(_uid("b"), key, "item.dispatch")
    first = _RecordingHandler()
    second = _RecordingHandler()

    counts = await asyncio.gather(
        _dispatcher(store, first).run_once(),
        _dispatcher(store, second).run_once(),
    )

    assert sum(counts) == 1
    assert len(first.delivered) + len(second.delivered) == 1


@pytest.mark.asyncio
async def test_stoplist_role_queues_are_skipped(store: SqlAlchemyAgentQueueStore) -> None:
    """A stopped role keeps its items queued; other roles still dispatch."""
    worker = _key("slot-a")
    manager = AgentQueueKey(role="manager", owner_user_id=_OWNER, scope_id=_uid("slot-b"))
    store.enqueue(_uid("a"), worker, "item.dispatch")
    store.enqueue(_uid("b"), manager, "item.dispatch")
    store.set_role_dispatch_stopped("manager", True)

    def _two_role_dispatcher(handler: RoleDispatchHandler) -> AgentQueueDispatcher:
        return AgentQueueDispatcher(
            DispatcherContext(
                store=store,
                handlers={"worker": handler, "manager": handler},
                read_status=_FakeStatus("idle"),
                grace_period_s=0.0,
            )
        )

    handler = _RecordingHandler()
    assert await _two_role_dispatcher(handler).run_once() == 1
    assert [item.id for item in handler.delivered] == [_uid("a")]

    # The stopped role's queue is untouched: item queued, nothing in flight.
    manager_item = store.get_item(_uid("b"))
    assert manager_item is not None
    assert manager_item.state == "queued"
    manager_queue = store.get_queue(manager)
    assert manager_queue is not None
    assert manager_queue.inflight_item_id is None

    # Re-enabling resumes on its own — no user resume needed.
    store.set_role_dispatch_stopped("manager", False)
    assert await _two_role_dispatcher(handler).run_once() == 1
    assert [item.id for item in handler.delivered] == [_uid("a"), _uid("b")]
