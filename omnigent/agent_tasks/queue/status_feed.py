"""Status feed — session idle/failed edges complete a role's in-flight item.

For worker roles the completion signal is :func:`notify_worker_session_status`, which
fires when a worker execution reaches a terminal state and is tied to a specific task
item. For the *broker* (and later the manager) the agent runs a long-lived session
and there is no per-item execution record, so completion is inferred from the session
going idle again after a dispatch: the item was handed to the agent, the agent worked on
it, and when the session returns to ``idle`` the item is done.

This module is the bridge: :func:`notify` is called on every session status change
(alongside the existing worker notifier), and for a non-worker session it completes
whatever that session's queue has in flight. It also feeds the same reading into the
dispatch gate so the dispatcher's next scan sees the fresh idle.

A failed session completes the in-flight item too — the agent will not produce more
output — and the dispatcher's gate resolves a failed session to ``ABANDON``, halting
the queue.
"""

from __future__ import annotations

import asyncio
import logging

from omnigent.agent_tasks.queue.gate import FAILED_STATUS, QUIET_STATUS
from omnigent.db.utils import now_epoch
from omnigent.stores.agent_queue_store import AgentQueueStore

_logger = logging.getLogger(__name__)

# Statuses that mean "the agent finished its turn". ``waiting`` is *not* here: it means
# the turn ended but sub-agents are still running, so the broker is still busy.
_TERMINAL_STATUSES = frozenset({QUIET_STATUS, FAILED_STATUS})


class QueueStatusFeed:
    """Bridge session status changes to the agent queue store and dispatch gate.

    Holds a reference to the store (for ``complete_inflight_for_session``) and an
    optional callback to push the same reading into the gate. The gate callback is
    injected rather than the feed holding the gate directly, so this module stays
    free of the dispatcher's lifecycle.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        *,
        on_status: _StatusObserver | None = None,
    ) -> None:
        self._store = store
        self._on_status = on_status

    async def notify(self, session_id: str, status: str) -> None:
        """Observe a session status change.

        Always feeds the gate (cheap, in-memory) and, for a terminal status, completes
        any in-flight item for that session. ``complete_inflight_for_session`` is a
        no-op when there is nothing in flight, so this is safe to call for every
        session — worker sessions included — without distinguishing roles.

        When a session goes idle and its queue was halted (retries exhausted),
        the user sending a message and getting a response proves the session
        is healthy — recover the queue and re-queue parked items.
        """
        if self._on_status is not None:
            self._on_status(session_id, status)
        if status not in _TERMINAL_STATUSES:
            return
        now = now_epoch()
        try:
            item = await asyncio.to_thread(
                self._store.complete_inflight_for_session,
                session_id,
                now=now,
            )
        except Exception:
            _logger.exception(
                "status feed: complete_inflight_for_session(%s) failed",
                session_id,
            )
            return
        if item is not None:
            _logger.debug(
                "status feed: completed in-flight item %s for session %s (%s)",
                item.id,
                session_id,
                status,
            )
        if status == QUIET_STATUS:
            try:
                recovered = await asyncio.to_thread(
                    self._store.recover_halted_queue_for_session,
                    session_id,
                    now=now,
                )
            except Exception:
                _logger.exception(
                    "status feed: recover_halted_queue_for_session(%s) failed",
                    session_id,
                )
                return
            if recovered:
                _logger.info(
                    "status feed: recovered halted queue for session %s, re-queued %d items",
                    session_id,
                    recovered,
                )


# A callable that pushes a status reading into the dispatch gate.
_StatusObserver = "callable[[str, str], None]"
