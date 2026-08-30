"""Agent-queue dispatcher — scan, lease, gate, dispatch, release.

One loop serves every agent. Queues with pending work are scanned, leased so a
second replica cannot drain the same one, checked against the dispatch gate, and
handed at most one item each.

Two properties are worth stating explicitly because they shape the code:

* **A lease is not held for the item's duration.** An item may run for hours. The
  dispatcher marks it in flight, releases the lease, and lets the completion
  signal re-arm the queue. Holding the lease would stall other queues behind a
  bounded pool slot and expire mid-run anyway.
* **A failed dispatch retries with capped exponential backoff.** A dispatch
  failure is often transient — the runner hasn't reconnected after a
  restart, the host is briefly offline. The item is re-queued with
  ``not_before = now + backoff`` (exponential from ``_BASE_BACKOFF_S``,
  capped at ``_MAX_BACKOFF_S``) and retried indefinitely, so a restart
  that brings the runner back heals the queue on its own. Only
  non-retryable failures park the item and halt the queue, and only a
  user resumes a permanently halted queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from omnigent.agent_tasks.queue.gate import (
    ABANDON,
    DEFAULT_GRACE_PERIOD_S,
    DISPATCH,
    DispatchGate,
)
from omnigent.db.utils import now_epoch
from omnigent.entities import AgentQueue, AgentQueueItem, AgentQueueKey
from omnigent.stores.agent_queue_store import AgentQueueStore

_logger = logging.getLogger(__name__)

# How long a lease is valid. Long enough to cover a slow dispatch, short enough
# that a dispatcher killed mid-item frees the queue promptly.
LEASE_TTL_S = 30

# Idle interval between scans when there is nothing to do.
SCAN_INTERVAL_S = 1.0

# An item in flight longer than this is presumed to have lost its completion
# signal, and the watchdog re-arms the queue rather than leaving it wedged.
MAX_INFLIGHT_S = 6 * 60 * 60

# Retry backoff for transient dispatch failures (runner not connected yet,
# host briefly offline, etc). The item is re-queued with not_before = now +
# backoff and retried indefinitely — exponential from _BASE_BACKOFF_S, capped
# at _MAX_BACKOFF_S so a long outage retries at most every 5 minutes and a
# restart that brings everything back is never blocked on a user resuming
# the queue. Only non-retryable failures park and halt.
_BASE_BACKOFF_S = 30  # 30s, 60s, 120s, 240s, then 300s
_MAX_BACKOFF_S = 5 * 60

# Concurrent queue drains, and the knob for global dispatch pressure. With
# thousands of tasks — each a manager queue — a task per queue is not affordable.
DEFAULT_POOL_SIZE = 8

# Queues scanned per pass.
SCAN_BATCH = 100


@dataclass(frozen=True)
class DispatchTarget:
    """
    Where an item should be delivered.

    :param session_id: Session whose quiet the gate measures, or ``None`` when
        there is nothing running to wait for. For a worker this is the slot's
        *current* session — the previous item's — because a worker dispatch
        creates a fresh conversation, and a brand-new one is idle by definition,
        which would make the gate a no-op.
    :param harness: Harness name, used to pick the grace period. ``None`` falls
        back to the default.
    """

    session_id: str | None
    harness: str | None = None
    ready: bool = True


class DispatchFailed(Exception):
    """A dispatch could not be handed to the agent. Halts that agent's queue."""


class RoleDispatchHandler(ABC):
    """Per-role delivery. The dispatcher knows *when* to send; this knows *how*."""

    @abstractmethod
    async def resolve_target(self, item: AgentQueueItem) -> DispatchTarget:
        """Return where this item goes.

        :raises DispatchFailed: If the target cannot be resolved at all, e.g. the
            worker slot or its task no longer exists.
        """

    @abstractmethod
    async def deliver(self, item: AgentQueueItem, target: DispatchTarget) -> None:
        """Hand the item to the agent.

        :raises DispatchFailed: If the item could not be delivered.
        """

    async def on_parked(self, item: AgentQueueItem, state: str) -> None:
        """Mirror a parked queue item onto whatever work record backs it.

        A parked item (``dispatch_failed`` or ``interrupted``) halts the queue
        and waits for the user, so anything the user actually looks at has to
        say the same thing — otherwise the board shows work still queued while
        its slot is stopped. Default is a no-op: only roles with a durable
        record behind the queue entry have something to mirror.
        """
        _logger.debug(
            "agent queue %s: no work record to mark %s for item %s",
            item.role,
            state,
            item.id,
        )


class StatusReader(ABC):
    """Reads the published status of a session."""

    @abstractmethod
    async def status_for(self, session_id: str) -> str | None:
        """Return ``"idle"``/``"running"``/``"waiting"``/``"failed"``, or ``None``.

        ``None`` means the status could not be determined, and the gate falls
        back to what it last observed. Implementations should prefer an
        authoritative read, because a status *cache* miss reads as idle and would
        otherwise make every queue look dispatchable after a restart.
        """


@dataclass
class DispatcherContext:
    """Collaborators the dispatcher needs to run."""

    store: AgentQueueStore
    handlers: Mapping[str, RoleDispatchHandler]
    read_status: StatusReader
    grace_period_s: float = DEFAULT_GRACE_PERIOD_S
    grace_overrides: Mapping[str, float] | None = None
    pool_size: int = DEFAULT_POOL_SIZE
    lease_owner: str = "dispatcher"


@dataclass
class _DrainOutcome:
    """Result of trying to drain one queue.

    Carried back rather than stashed on the dispatcher because queues drain
    concurrently, and a backoff computed for one must not leak into another.
    """

    dispatched: bool = False
    next_due_at: int | None = None


class AgentQueueDispatcher:
    """Drains every agent queue, one item at a time per agent."""

    def __init__(self, context: DispatcherContext) -> None:
        self._context = context
        self._gate = DispatchGate(
            started_at=time.monotonic(),
            grace_period_s=context.grace_period_s,
            grace_overrides=context.grace_overrides,
        )
        self._semaphore = asyncio.Semaphore(context.pool_size)
        self._task: asyncio.Task[None] | None = None

    @property
    def gate(self) -> DispatchGate:
        """The dispatch gate, exposed so a status feed can push observations."""
        return self._gate

    async def run_once(self) -> int:
        """Run one scan pass. Returns how many items were dispatched."""
        store = self._context.store
        now = now_epoch()
        reclaimed = await asyncio.to_thread(
            store.reclaim_stale_inflight,
            now=now,
            max_inflight_s=MAX_INFLIGHT_S,
        )
        for item in reclaimed:
            _logger.warning(
                "agent queue %s/%s/%s: item %s lost its agent, parked as interrupted",
                item.role,
                item.owner_user_id,
                item.scope_id,
                item.id,
            )
            await self._notify_parked(item, "interrupted")
        queues = await asyncio.to_thread(store.due_queues, now=now, limit=SCAN_BATCH)
        if not queues:
            return 0
        # The global stoplist (board config panel) silences whole roles: their
        # queues stay in place with items queued, and come back on their own
        # when the role is re-enabled. Read once per pass — one cheap lookup.
        stopped = await asyncio.to_thread(store.get_dispatch_stoplist)
        if stopped:
            skipped = [queue for queue in queues if queue.role in stopped]
            queues = [queue for queue in queues if queue.role not in stopped]
            if skipped:
                _logger.debug(
                    "agent queue dispatcher: skipping stopped roles %s (%d queues)",
                    sorted(stopped),
                    len(skipped),
                )
            if not queues:
                return 0
        results = await asyncio.gather(
            *(self._drain_one(queue) for queue in queues),
            return_exceptions=True,
        )
        dispatched = 0
        for result in results:
            if isinstance(result, BaseException):
                _logger.exception("agent queue drain failed", exc_info=result)
                continue
            dispatched += int(result)
        return dispatched

    async def run_forever(self) -> None:
        """Scan until cancelled."""
        while True:
            try:
                dispatched = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("agent queue scan failed")
                dispatched = 0
            if not dispatched:
                await asyncio.sleep(SCAN_INTERVAL_S)

    async def start(self) -> None:
        """Start the background scan loop."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self.run_forever(),
            name="agent-queue-dispatcher",
        )

    async def stop(self) -> None:
        """Cancel the background scan loop and wait for it to unwind."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _drain_one(self, queue: AgentQueue) -> bool:
        """Try to dispatch one item from *queue*. Returns whether one went out."""
        async with self._semaphore:
            store = self._context.store
            key = queue.key
            leased = await asyncio.to_thread(
                store.acquire_lease,
                key,
                self._context.lease_owner,
                now=now_epoch(),
                ttl_s=LEASE_TTL_S,
            )
            if leased is None:
                return False
            outcome = _DrainOutcome()
            try:
                outcome = await self._dispatch_head(key)
            finally:
                # Released unconditionally: the in-flight marker, not the lease,
                # is what keeps the next item from going out.
                await asyncio.to_thread(
                    store.release_lease,
                    key,
                    self._context.lease_owner,
                    next_due_at=outcome.next_due_at,
                )
            return outcome.dispatched

    async def _dispatch_head(self, key: AgentQueueKey) -> _DrainOutcome:
        store = self._context.store
        item = await asyncio.to_thread(store.next_dispatchable_item, key, now=now_epoch())
        if item is None:
            return _DrainOutcome()

        handler = self._context.handlers.get(item.role)
        if handler is None:
            # The role is not enabled in this deployment — a configuration gap,
            # not a broken agent — so hold the item instead of halting the queue.
            _logger.debug("no dispatch handler for role %r; holding item", item.role)
            return _DrainOutcome(next_due_at=now_epoch() + int(SCAN_INTERVAL_S))

        try:
            target = await handler.resolve_target(item)
            gate_result = await self._check_gate(target)
            if not gate_result.ready:
                return _DrainOutcome(next_due_at=gate_result.next_due_at)
        except DispatchFailed as exc:
            await self._fail(item, key, str(exc))
            return _DrainOutcome()

        dispatched = await asyncio.to_thread(store.mark_dispatched, item.id, key, now=now_epoch())
        if dispatched is None:
            # Another dispatcher claimed the in-flight slot first.
            return _DrainOutcome()
        if dispatched.retry_count:
            # A retry going out: say so, with why it failed before, so a queue
            # stuck in a retry loop is visible in the logs without digging.
            _logger.info(
                "agent queue %s/%s/%s: retrying item %s (attempt %d, last error: %s)",
                key.role,
                key.owner_user_id,
                key.scope_id,
                dispatched.id,
                dispatched.retry_count + 1,
                dispatched.last_error or "unknown",
            )

        try:
            await handler.deliver(dispatched, target)
        except DispatchFailed as exc:
            await self._fail(dispatched, key, str(exc))
            return _DrainOutcome()
        except Exception as exc:  # noqa: BLE001 - the item is already in flight
            # Anything escaping here would leave the item marked in flight with
            # no completion signal coming, wedging the queue until the watchdog.
            await self._fail(dispatched, key, f"unexpected dispatch error: {exc}")
            return _DrainOutcome()
        return _DrainOutcome(dispatched=True)

    async def _check_gate(self, target: DispatchTarget) -> _GateResult:
        """Feed the gate a fresh status reading and rule on it.

        :raises DispatchFailed: If the target will never become quiet.
        """
        if not target.ready:
            return _GateResult(ready=False, next_due_at=now_epoch() + 1)
        if target.session_id is None:
            return _GateResult(ready=True)
        status = await self._context.read_status.status_for(target.session_id)
        now = time.monotonic()
        if status is not None:
            self._gate.observe(target.session_id, status, now=now)
        decision = self._gate.evaluate(target.session_id, now=now, harness=target.harness)
        if decision.action == DISPATCH:
            return _GateResult(ready=True)
        if decision.action == ABANDON:
            raise DispatchFailed(decision.reason or "target session unavailable")
        return _GateResult(
            ready=False,
            next_due_at=now_epoch() + max(1, round(decision.retry_after_s)),
        )

    async def _fail(self, item: AgentQueueItem, key: AgentQueueKey, error: str) -> None:
        backoff = min(_BASE_BACKOFF_S * (2 ** item.retry_count), _MAX_BACKOFF_S)
        _logger.warning(
            "agent queue %s/%s/%s: dispatch failed (attempt %d, retrying in %ds): %s",
            key.role,
            key.owner_user_id,
            key.scope_id,
            item.retry_count + 1,
            backoff,
            error,
        )
        failed = await asyncio.to_thread(
            self._context.store.fail_dispatch,
            item.id,
            key,
            error=error,
            now=now_epoch(),
            retryable=True,
            max_retries=None,
            backoff_s=backoff,
        )
        # Mirror only a true park onto the work record: a requeued item is
        # still being retried, and marking its task dispatch_failed would
        # smear the board every cycle and outlive a successful retry.
        if failed is not None and failed.state == "dispatch_failed":
            await self._notify_parked(item, "dispatch_failed")

    async def _notify_parked(self, item: AgentQueueItem, state: str) -> None:
        """Let the owning role mirror a park onto its own work record."""
        handler = self._context.handlers.get(item.role)
        if handler is None:
            return
        try:
            await handler.on_parked(item, state)
        except Exception:
            _logger.exception(
                "agent queue %s: could not mirror %s onto item %s",
                item.role,
                state,
                item.id,
            )


@dataclass(frozen=True)
class _GateResult:
    ready: bool
    next_due_at: int | None = None
