"""Agent-queue persistence — one queue per agent, one item in flight.

Split across three concerns:

* **Producers** (stage-1 packagers) call :meth:`AgentQueueStore.enqueue` and use
  :meth:`list_claimed_source_ids` to avoid packaging the same business signal
  twice.
* **The dispatcher** scans with :meth:`due_queues`, takes a lease, pulls the head
  of the queue, and reports the outcome.
* **The control plane** pauses, resumes, edits, cancels, and snoozes.

The queue — not the business item's own state — is the source of truth for what
gets dispatched. A worker that ran and failed returns to the ``queued`` item
state with no queue item, so it is never silently retried.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import AgentQueue, AgentQueueItem, AgentQueueKey

_UNSET: Any = object()


class AgentQueueStore(ABC):
    """Abstract base for agent-queue and queue-item persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    # ── Producers ──────────────────────────────────────

    @abstractmethod
    def enqueue(
        self,
        item_id: str,
        key: AgentQueueKey,
        kind: str,
        *,
        source_ids: list[str] | None = None,
        payload: str | None = None,
        priority: int = 0,
        not_before: int | None = None,
    ) -> AgentQueueItem:
        """Append one agent-ready item, creating the queue row if needed.

        Creating the queue row here — rather than lazily at dispatch — means an
        enqueue onto a paused or halted queue is retained rather than silently
        reactivating it.
        """

    @abstractmethod
    def list_claimed_source_ids(
        self,
        role: str,
        owner_user_id: str,
        *,
        scope_id: str | None = None,
    ) -> set[str]:
        """Return source ids referenced by items that have not yet completed.

        Packagers exclude these so a crash between packaging and dispatch
        neither loses a signal nor packages it a second time.
        """

    # ── Dispatcher ─────────────────────────────────────

    @abstractmethod
    def due_queues(self, *, now: int, limit: int = 100) -> list[AgentQueue]:
        """Return active queues with dispatchable work and no live lease.

        Excludes paused and halted queues, queues with an item already in
        flight, and queues whose ``next_due_at`` is still in the future.
        """

    @abstractmethod
    def acquire_lease(
        self,
        key: AgentQueueKey,
        lease_owner: str,
        *,
        now: int,
        ttl_s: int,
    ) -> AgentQueue | None:
        """Take the lease on a queue, or return ``None`` if another holds it.

        A lease whose ``lease_expires_at`` has passed may be stolen, so a
        dispatcher that dies mid-item does not wedge the queue forever.
        """

    @abstractmethod
    def renew_lease(
        self,
        key: AgentQueueKey,
        lease_owner: str,
        *,
        now: int,
        ttl_s: int,
    ) -> bool:
        """Extend a lease held by *lease_owner*. Returns whether it still held."""

    @abstractmethod
    def release_lease(
        self,
        key: AgentQueueKey,
        lease_owner: str,
        *,
        next_due_at: int | None = None,
    ) -> None:
        """Drop a lease and optionally set when the queue is next worth scanning."""

    @abstractmethod
    def next_dispatchable_item(
        self,
        key: AgentQueueKey,
        *,
        now: int,
    ) -> AgentQueueItem | None:
        """Return the head of the queue, or ``None`` if nothing is ready.

        Ordered by priority then arrival. Items snoozed past *now* are skipped.
        """

    @abstractmethod
    def mark_dispatched(
        self,
        item_id: str,
        key: AgentQueueKey,
        *,
        now: int,
    ) -> AgentQueueItem | None:
        """Mark an item handed to the agent and record it as the queue's in-flight.

        Returns ``None`` if the queue already had something in flight, which is
        how the one-at-a-time guarantee survives two dispatchers racing.
        """

    @abstractmethod
    def complete_inflight(
        self,
        key: AgentQueueKey,
        *,
        item_id: str | None = None,
        now: int,
    ) -> AgentQueueItem | None:
        """Finish the in-flight item and re-arm the queue.

        Passing *item_id* makes the completion conditional, so a late signal
        from a previous item cannot clear a newer one.
        """

    @abstractmethod
    def fail_dispatch(
        self,
        item_id: str,
        key: AgentQueueKey,
        *,
        error: str,
        now: int,
    ) -> AgentQueueItem | None:
        """Record a failed dispatch and halt the queue.

        There is no retry: a dispatch failure almost always means the agent's
        environment is broken, so the next item would fail identically. Only a
        user resumes the queue.
        """

    @abstractmethod
    def reclaim_stale_inflight(self, *, now: int, max_inflight_s: int) -> list[AgentQueue]:
        """Clear in-flight items older than *max_inflight_s* and return their queues.

        A lost idle edge would otherwise wedge a queue permanently; the watchdog
        re-arms it instead.
        """

    # ── Control plane ──────────────────────────────────

    @abstractmethod
    def get_queue(self, key: AgentQueueKey) -> AgentQueue | None:
        """Return one queue by identity."""

    @abstractmethod
    def list_queues(
        self,
        *,
        role: str | None = None,
        owner_user_id: str | None = None,
        state: str | None = None,
    ) -> list[AgentQueue]:
        """List queues, most recently updated first."""

    @abstractmethod
    def queue_depth(self, key: AgentQueueKey) -> int:
        """Return the number of items still waiting on a queue."""

    @abstractmethod
    def set_queue_state(
        self,
        key: AgentQueueKey,
        state: str,
        *,
        last_error: str | None = _UNSET,
    ) -> AgentQueue | None:
        """Set a queue to ``active``, ``paused``, or ``halted``.

        Resuming to ``active`` clears ``last_error`` unless one is passed.
        """

    @abstractmethod
    def get_item(self, item_id: str) -> AgentQueueItem | None:
        """Return one queue item by id."""

    @abstractmethod
    def list_items(
        self,
        key: AgentQueueKey,
        *,
        state: str | None = None,
        limit: int | None = None,
    ) -> list[AgentQueueItem]:
        """List a queue's items in dispatch order."""

    @abstractmethod
    def update_item(
        self,
        item_id: str,
        *,
        payload: str | None = _UNSET,
        priority: int | None = None,
        not_before: int | None = _UNSET,
    ) -> AgentQueueItem | None:
        """Edit a queued item before it is dispatched.

        Rejects edits to an item that already left the queue, so a payload
        cannot change out from under a running agent.
        """

    @abstractmethod
    def cancel_item(self, item_id: str, *, now: int) -> AgentQueueItem | None:
        """Drop a queued item. The only way past a poisoned head-of-line item."""
