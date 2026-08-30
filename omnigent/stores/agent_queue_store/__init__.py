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

        Strict insert order — there is no priority. Items snoozed past *now* are
        skipped.
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
        retryable: bool = False,
        max_retries: int = 0,
        backoff_s: int = 0,
    ) -> AgentQueueItem | None:
        """Record a failed dispatch.

        When ``retryable`` and retries remain: re-queue the item with a
        ``not_before`` backoff and keep the queue ``active`` so the
        dispatcher retries after the delay. When retries are exhausted or
        the failure is not retryable: park the item as ``dispatch_failed``
        and halt the queue (user must resume).
        """

    @abstractmethod
    def reclaim_stale_inflight(self, *, now: int, max_inflight_s: int) -> list[AgentQueueItem]:
        """Park in-flight items older than *max_inflight_s* and return them.

        A lost idle edge would otherwise wedge a queue permanently. The item is
        parked as ``interrupted`` and its queue halted rather than completed,
        because the agent went away mid-item: recording that as ``done`` would
        file unfinished work as finished and leave nothing for the user to
        retry.
        """

    @abstractmethod
    def set_queue_conversation(
        self,
        key: AgentQueueKey,
        conversation_id: str,
    ) -> AgentQueue | None:
        """Cache the delivery target on the queue row.

        Set by the dispatch handler when it resolves the target, so the status feed
        can reverse-look-up a queue from a session id.
        """

    @abstractmethod
    def complete_inflight_for_session(
        self,
        session_id: str,
        *,
        now: int,
    ) -> AgentQueueItem | None:
        """Complete the in-flight item for the queue targeting *session_id*.

        A no-op when the session has no queue or nothing in flight, so it is safe to
        call for every session status change.
        """

    @abstractmethod
    def recover_halted_queue_for_session(
        self,
        session_id: str,
        *,
        now: int,
    ) -> int:
        """Re-arm a halted queue and re-queue its parked items.

        Called when the session goes idle after being halted — the user sent
        a message and got a response, proving the session is healthy. Finds
        the queue by its cached conversation_id, un-halts it, and re-queues
        any parked (``dispatch_failed`` / ``interrupted``) items with fresh
        retry counts.

        :returns: Number of items re-queued.
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
    def acquire_inspection_hold(
        self,
        key: AgentQueueKey,
        token: str,
        *,
        now: int,
        ttl_s: int,
    ) -> AgentQueue:
        """Temporarily block new dispatches without changing persistent queue state."""

    @abstractmethod
    def release_inspection_hold(self, key: AgentQueueKey, token: str) -> bool:
        """Release a matching temporary inspection hold."""

    @abstractmethod
    def get_item(self, item_id: str) -> AgentQueueItem | None:
        """Return one queue item by id."""

    @abstractmethod
    def find_open_item_for_source(
        self, source_id: str, *, role: str | None = None
    ) -> AgentQueueItem | None:
        """Find the newest open queue delivery claiming a business source id."""

    @abstractmethod
    def list_open_items_for_role(self, role: str) -> list[AgentQueueItem]:
        """List every non-terminal item for a role, across owners and scopes.

        Open means not ``done`` and not ``cancelled``. Used by migrations that
        re-key a role's queues and must cancel derived items so their sources
        re-package under the new keys.
        """

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
    def acquire_item_edit_lease(
        self,
        item_id: str,
        token: str,
        *,
        now: int,
        ttl_s: int,
    ) -> AgentQueueItem | None:
        """Hold one queued item so its payload cannot dispatch while edited."""

    @abstractmethod
    def release_item_edit_lease(self, item_id: str, token: str) -> bool:
        """Release a matching queued-item edit lease."""

    @abstractmethod
    def update_item(
        self,
        item_id: str,
        *,
        payload: str | None = _UNSET,
        not_before: int | None = _UNSET,
        edit_lease_token: str | None = None,
    ) -> AgentQueueItem | None:
        """Edit a queued item before it is dispatched.

        Rejects edits to an item that already left the queue, so a payload
        cannot change out from under a running agent.
        """

    @abstractmethod
    def retry_parked_item(self, item_id: str, *, now: int) -> AgentQueueItem | None:
        """Return an interrupted or dispatch-failed item to its active queue."""

    @abstractmethod
    def cancel_item(self, item_id: str, *, now: int) -> AgentQueueItem | None:
        """Drop a queued or parked item.

        For a parked item — ``dispatch_failed`` or ``interrupted``, the one that
        halted the queue — cancel also clears the halt, so it is a complete
        recovery and not a two-step resume. Idempotent on already-``cancelled``
        items. Returns ``None`` for items that are not found or are in flight /
        done (not cancelable).
        """

    # ── GC ─────────────────────────────────────────────

    @abstractmethod
    def purge_old_items(self, *, before_ts: int, states: list[str]) -> int:
        """Delete queue items in the given states older than ``before_ts``.

        :returns: Number of rows deleted.
        """
