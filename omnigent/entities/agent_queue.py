"""Agent-queue entities — one queue per agent, dispatched one item at a time.

An :class:`AgentQueue` is the per-agent control row (pause state, lease,
in-flight item). :class:`AgentQueueItem` rows are the agent-ready work units
that stage-1 packaging produced and the dispatcher hands over one at a time.
"""

from __future__ import annotations

from dataclasses import dataclass

# Recipient roles that own a queue. The ingress scorer (``ingress.py``) is
# deterministic Python with no session, so it never appears here; the secretary
# is a chat-only assistant the user talks to directly, so it has no queue either.
AGENT_QUEUE_ROLES = frozenset({"broker", "manager", "worker"})

# Payload shapes. A "notice" is prose packaged from N business signals; an
# "item.dispatch" is an instruction to start one task item on a worker slot.
AGENT_QUEUE_ITEM_KINDS = frozenset({"notice", "item.dispatch"})


@dataclass(frozen=True)
class AgentQueueKey:
    """
    Identity of one agent queue.

    ``scope_id`` narrows the role to a single agent: the task id for a manager,
    the worker id for a worker slot, and ``None`` for per-user roles such as the
    broker, which have exactly one queue per owner.

    :param role: One of :data:`AGENT_QUEUE_ROLES`.
    :param owner_user_id: Owning user, or ``""`` in single-user mode.
    :param scope_id: Task/worker id narrowing the role, or ``None``.
    """

    role: str
    owner_user_id: str
    scope_id: str | None = None


@dataclass
class AgentQueue:
    """
    Per-agent control row in the ``agent_queues`` table.

    :param role: One of :data:`AGENT_QUEUE_ROLES`.
    :param owner_user_id: Owning user, or ``""`` in single-user mode.
    :param scope_id: Task/worker id narrowing the role, or ``None``.
    :param state: ``"active"``, ``"paused"`` (by a user), or ``"halted"`` (by a
        failed dispatch). Only a user clears either stopped state.
    :param created_at: Unix epoch seconds at row creation.
    :param conversation_id: Cached delivery target, or ``None`` before bind.
    :param lease_owner: Dispatcher replica currently draining this queue, or
        ``None`` when unleased.
    :param lease_expires_at: Unix epoch seconds after which the lease is stale
        and may be stolen. ``None`` when unleased.
    :param next_due_at: Earliest Unix epoch second worth re-scanning this queue,
        used for debounce and grace-window backoff. ``None`` means due now.
    :param inflight_item_id: Item handed to the agent and not yet complete, or
        ``None`` when the queue is free to dispatch.
    :param inflight_since: Unix epoch seconds the in-flight item was dispatched,
        used by the watchdog. ``None`` when nothing is in flight.
    :param last_error: Why the queue halted. ``None`` unless halted.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    role: str
    owner_user_id: str
    scope_id: str | None
    state: str
    created_at: int
    conversation_id: str | None = None
    lease_owner: str | None = None
    lease_expires_at: int | None = None
    inspection_hold_token: str | None = None
    inspection_hold_expires_at: int | None = None
    next_due_at: int | None = None
    inflight_item_id: str | None = None
    inflight_since: int | None = None
    last_error: str | None = None
    updated_at: int | None = None

    @property
    def key(self) -> AgentQueueKey:
        """Return the identity tuple for this queue."""
        return AgentQueueKey(
            role=self.role,
            owner_user_id=self.owner_user_id,
            scope_id=self.scope_id,
        )


@dataclass
class AgentQueueItem:
    """
    One agent-ready work unit in the ``agent_queue_items`` table.

    ``payload`` holds the *inputs* a dispatcher renders at send time, never
    pre-rendered text — that is what lets the control plane edit a queued item
    and have the edit take effect.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param role: Recipient role, one of :data:`AGENT_QUEUE_ROLES`.
    :param owner_user_id: Owning user, or ``""`` in single-user mode.
    :param scope_id: Task/worker id narrowing the role, or ``None``.
    :param kind: One of :data:`AGENT_QUEUE_ITEM_KINDS`.
    :param state: ``"queued"``, ``"dispatched"``, ``"done"``, ``"cancelled"``,
        ``"dispatch_failed"``, or ``"interrupted"``. The last two are *parked*:
        the queue halts and the item waits to be retried or cancelled.
    :param created_at: Unix epoch seconds at row creation.
    :param source_ids: Business-layer ids (events, task items) this item
        consumed. Packaging commits these before the sources are treated as
        claimed, so a crash mid-package neither loses nor double-packages work.
    :param payload: JSON-encoded dispatch inputs. ``None`` when the kind needs
        none.
    :param seq: Monotonic arrival order, assigned at enqueue, and the only
        ordering a queue has — items dispatch in the order they were inserted.
        Breaks ties that ``created_at`` cannot, being second-granularity.
    :param not_before: Earliest Unix epoch second this item may dispatch, used
        for debounce and snooze. There is no retry backoff — a failed dispatch
        halts the queue rather than rescheduling.
    :param last_error: Why the dispatch failed. ``None`` unless failed.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    :param dispatched_at: Unix epoch seconds the item was handed to the agent,
        or ``None``.
    :param completed_at: Unix epoch seconds the item reached a terminal state,
        or ``None``.
    """

    id: str
    role: str
    owner_user_id: str
    scope_id: str | None
    kind: str
    state: str
    created_at: int
    source_ids: list[str] | None = None
    payload: str | None = None
    seq: int = 0
    not_before: int | None = None
    retry_count: int = 0
    edit_lease_token: str | None = None
    edit_lease_expires_at: int | None = None
    last_error: str | None = None
    updated_at: int | None = None
    dispatched_at: int | None = None
    completed_at: int | None = None

    @property
    def key(self) -> AgentQueueKey:
        """Return the identity of the queue this item belongs to."""
        return AgentQueueKey(
            role=self.role,
            owner_user_id=self.owner_user_id,
            scope_id=self.scope_id,
        )
