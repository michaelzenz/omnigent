"""Stage-1 packagers — poll business state, batch, and enqueue agent-ready items.

Each packager is a **stateless periodic scanner**: every ``poll_interval_s`` it reads
business state (e.g. events in ``awaiting_grouping``), groups pending signals by
:class:`AgentQueueKey`, and for each key evaluates a cost-aware batching matrix:

| pending count | agent status | oldest age | action |
|---|---|---|---|
| ``>= batch_size`` | any | any | **send** (don't hold a full batch) |
| ``< batch_size`` | busy | any | **wait** (let more land → bigger batch → fewer turns) |
| ``< batch_size`` | idle | ``> age_threshold_s`` | **send** (agent ready, floor reached) |
| ``< batch_size`` | idle | ``<= age_threshold_s`` | **wait** (give more time to accumulate) |

There is no in-memory signal queue and no push: the poll re-reads durable business
state each tick, so a crash between a business event arriving and packaging
neither loses the signal (the event row survives) nor double-packages it
(``list_claimed_source_ids`` excludes already-claimed events). This closes the
durability gap the push model had.

The worker role skips the packager entirely — one task item → one queue item,
identity — so its trigger calls ``store.enqueue`` directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from omnigent.agent_tasks.agent_builtins import (
    TASK_MANAGER_ROLE,
    TASK_SECRETARY_ROLE,
)
from omnigent.agent_tasks.constants import (
    MANAGER_BATCH_MAX_SIZE,
    SECRETARY_BATCH_MAX_SIZE,
    SECRETARY_CANDIDATE_LIMIT,
    SECRETARY_TAG_SIMILARITY_THRESHOLD,
)
from omnigent.agent_tasks.event_types import SESSION_ORPHAN_EVENT_TYPE
from omnigent.agent_tasks.notices import _format_manager_notice, _format_secretary_stall_notice
from omnigent.agent_tasks.secretary_inbox import (
    AmbiguousEventCluster,
    cluster_events_by_similarity,
)
from omnigent.agent_tasks.task_match import rank_tasks_for_events, routable_tasks
from omnigent.db.utils import now_epoch
from omnigent.entities import AgentQueueItem, AgentQueueKey, TaskEvent
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore

_logger = logging.getLogger(__name__)

# ── Configurable defaults (global constants for now) ───

# How often each packager scans business state.
DEFAULT_PACKAGER_POLL_INTERVAL_S = 5.0

# A partial batch waits this long (oldest event age) before it is flushed to a
# ready-but-not-full agent. Caps the "wait for more events" window.
DEFAULT_PACKAGER_AGE_THRESHOLD_S = 15

# ── Base class ───────────────────────────────────────


@dataclass
class _PendingBatch:
    """One key's accumulated pending signals within a scan pass."""

    key: AgentQueueKey
    events: list[TaskEvent] = field(default_factory=list)
    # Secretary-only metadata carried through to ``_flush`` so the notice can
    # carry the included clusters / orphan flag. For a routed batch this holds
    # the clusters packed into this notice (each possibly capped to ``batch_size``);
    # ``events`` is the flat union of those clusters' events. Other roles leave
    # these at their defaults.
    clusters: list[AmbiguousEventCluster] | None = None
    is_orphan: bool = False

    @property
    def oldest_age_s(self) -> float:
        return (now_epoch() - min(e.created_at for e in self.events)) if self.events else 0.0


class Packager(ABC):
    """Base class for stage-1 packagers.

    Owns a poll loop that scans business state each ``poll_interval_s``,
    evaluates the batching matrix per key, and enqueues one item for the keys
    that decide to send. One instance per role.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        *,
        poll_interval_s: float = DEFAULT_PACKAGER_POLL_INTERVAL_S,
        batch_size: int,
        age_threshold_s: float = DEFAULT_PACKAGER_AGE_THRESHOLD_S,
    ) -> None:
        self._store = store
        self._poll_interval_s = poll_interval_s
        self._batch_size = batch_size
        self._age_threshold_s = age_threshold_s
        self._task: asyncio.Task[None] | None = None

    @property
    @abstractmethod
    def role(self) -> str:
        """The role this packager feeds (e.g. ``"secretary"``)."""

    @abstractmethod
    def _collect_pending(self) -> list[_PendingBatch]:
        """Scan business state, return pending signals grouped by key.

        Excludes signals already claimed by an open queue item.
        """

    @abstractmethod
    def _is_idle(self, key: AgentQueueKey) -> bool:
        """Is the destination agent for *key* currently idle (raw status)?

        Returns ``False`` when there is no live agent session for the key, so a
        missing agent never triggers a partial-batch flush.
        """

    @abstractmethod
    def _flush(self, batch: _PendingBatch) -> AgentQueueItem | None:
        """Format the payload and enqueue one item. ``None`` if unpackageable."""

    def _should_send(self, batch: _PendingBatch) -> bool:
        """Evaluate the batching matrix for one key."""
        if not batch.events:
            return False
        if len(batch.events) >= self._batch_size:
            return True
        # Partial batch: wait unless the agent is idle and the floor is reached.
        if not self._is_idle(batch.key):
            return False
        return batch.oldest_age_s > self._age_threshold_s

    async def start(self) -> None:
        """Start the poll loop."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._poll_loop(),
            name=f"packager-{self.role}",
        )

    async def stop(self) -> None:
        """Cancel the poll loop and wait for it to unwind."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("packager %s scan failed", self.role)
            await asyncio.sleep(self._poll_interval_s)

    async def _scan_once(self) -> None:
        for batch in self._collect_pending():
            if self._should_send(batch):
                try:
                    self._flush(batch)
                except Exception:
                    _logger.exception(
                        "packager %s failed to flush %s",
                        self.role,
                        batch.key,
                    )

    def scan_once_sync(self) -> None:
        """Run one scan synchronously (tests only)."""
        for batch in self._collect_pending():
            if self._should_send(batch):
                self._flush(batch)


# ── Secretary packager ──────────────────────────────


class SecretaryPackager(Packager):
    """Stage-1 packager for secretary events.

    Scans ``awaiting_grouping`` events each tick, groups by owner, excludes
    already-claimed events, and for each owner splits orphans (``session.orphan``)
    from routed business events. Routed events are clustered by tag similarity
    (leader-based, oldest-first) so each notice is a focused batch of similar
    events carrying its own candidate task ids; each orphan is its own notice.
    The agent-idle check reads the secretary's bound session from the role
    profile and looks up its raw status.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        task_event_store: TaskEventStore,
        task_role_profile_store: TaskRoleProfileStore,
        task_store: TaskStore,
        status_reader: _StatusReader,
        *,
        poll_interval_s: float = DEFAULT_PACKAGER_POLL_INTERVAL_S,
        batch_size: int = SECRETARY_BATCH_MAX_SIZE,
        age_threshold_s: float = DEFAULT_PACKAGER_AGE_THRESHOLD_S,
        similarity_threshold: float = SECRETARY_TAG_SIMILARITY_THRESHOLD,
        candidate_limit: int = SECRETARY_CANDIDATE_LIMIT,
    ) -> None:
        super().__init__(
            store,
            poll_interval_s=poll_interval_s,
            batch_size=batch_size,
            age_threshold_s=age_threshold_s,
        )
        self._task_event_store = task_event_store
        self._task_role_profile_store = task_role_profile_store
        self._task_store = task_store
        self._status_reader = status_reader
        self._similarity_threshold = similarity_threshold
        self._candidate_limit = candidate_limit

    @property
    def role(self) -> str:
        return TASK_SECRETARY_ROLE

    def _collect_pending(self) -> list[_PendingBatch]:
        events = self._task_event_store.list_events(state="awaiting_grouping")
        if not events:
            return []
        # Group by owner; events without an owner fall back to "__anonymous__".
        grouped: dict[str, list[TaskEvent]] = {}
        for event in events:
            owner = event.owner_user_id or "__anonymous__"
            grouped.setdefault(owner, []).append(event)
        batches: list[_PendingBatch] = []
        for owner, owner_events in grouped.items():
            key = AgentQueueKey(role=TASK_SECRETARY_ROLE, owner_user_id=owner)
            claimed = self._store.list_claimed_source_ids(
                TASK_SECRETARY_ROLE,
                owner,
            )
            unclaimed = [e for e in owner_events if e.id not in claimed]
            if not unclaimed:
                continue
            orphans = [e for e in unclaimed if e.event_type == SESSION_ORPHAN_EVENT_TYPE]
            routed = [e for e in unclaimed if e.event_type != SESSION_ORPHAN_EVENT_TYPE]
            # Each orphan is its own batch — adoption is heavy and per-session.
            for orphan in orphans:
                batches.append(_PendingBatch(key=key, events=[orphan], is_orphan=True))
            # Routed events: cluster by tag similarity, then fill ONE notice per
            # poll up to ``batch_size``. Clusters are taken oldest-first; a cluster
            # that would overflow the remaining capacity is capped to its oldest
            # ``remaining`` events (the rest stay ``awaiting_grouping`` and are
            # re-clustered next poll). Similar events stay contiguous per cluster.
            clusters = cluster_events_by_similarity(
                routed, threshold=self._similarity_threshold
            )
            clusters.sort(key=lambda c: (c.events[0].created_at, c.events[0].id))
            included_clusters: list[AmbiguousEventCluster] = []
            included_events: list[TaskEvent] = []
            for cluster in clusters:
                remaining = self._batch_size - len(included_events)
                if remaining <= 0:
                    break
                if len(cluster.events) <= remaining:
                    take = cluster
                else:
                    take = AmbiguousEventCluster(
                        tags=cluster.tags, events=cluster.events[:remaining]
                    )
                included_clusters.append(take)
                included_events.extend(take.events)
            if included_events:
                batches.append(
                    _PendingBatch(
                        key=key,
                        events=included_events,
                        clusters=included_clusters,
                    )
                )
        return batches

    def _is_idle(self, key: AgentQueueKey) -> bool:
        profile = self._task_role_profile_store.get(
            key.owner_user_id,
            TASK_SECRETARY_ROLE,
        )
        if profile is None or profile.conversation_id is None:
            return False
        return self._status_reader.status_for(profile.conversation_id) == "idle"

    def _flush(self, batch: _PendingBatch) -> AgentQueueItem | None:
        profile = self._task_role_profile_store.get(
            batch.key.owner_user_id,
            TASK_SECRETARY_ROLE,
        )
        if profile is None or profile.conversation_id is None:
            _logger.debug(
                "secretary packager: no live secretary for %s; "
                "%d events stay in awaiting_grouping",
                batch.key.owner_user_id,
                len(batch.events),
            )
            return None
        candidate_task_ids: list[str] = []
        if not batch.is_orphan:
            ranked = rank_tasks_for_events(
                events=batch.events,
                tasks=routable_tasks(self._task_store),
                task_store=self._task_store,
                limit=self._candidate_limit,
            )
            candidate_task_ids = [task.id for task, _score in ranked]
        notice = _format_secretary_stall_notice(
            batch.events,
            clusters=batch.clusters,
            candidate_task_ids=candidate_task_ids,
            is_orphan=batch.is_orphan,
        )
        return self._store.enqueue(
            uuid.uuid4().hex,
            batch.key,
            "notice",
            source_ids=[event.id for event in batch.events],
            payload=notice,
        )


# ── Manager packager ────────────────────────────────


class ManagerPackager(Packager):
    """Stage-1 packager for routed task events.

    Scans ``routed`` events each tick — these are events the distributor (or the
    secretary resolve path) already bound to a task, plus ``worker.execution.finished``
    events the completion hook emits when a worker settles. They are grouped by
    ``(owner, task_id)`` and evaluated against the same batching matrix as the
    secretary. The agent-idle check reads the task's ``manager_conversation_id``
    and looks up its raw status; a task with no manager session yet is treated as
    not idle, so events stay routed until bootstrap.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        task_event_store: TaskEventStore,
        task_store: TaskStore,
        status_reader: _StatusReader,
        *,
        poll_interval_s: float = DEFAULT_PACKAGER_POLL_INTERVAL_S,
        batch_size: int = MANAGER_BATCH_MAX_SIZE,
        age_threshold_s: float = DEFAULT_PACKAGER_AGE_THRESHOLD_S,
    ) -> None:
        super().__init__(
            store,
            poll_interval_s=poll_interval_s,
            batch_size=batch_size,
            age_threshold_s=age_threshold_s,
        )
        self._task_event_store = task_event_store
        self._task_store = task_store
        self._status_reader = status_reader

    @property
    def role(self) -> str:
        return TASK_MANAGER_ROLE

    def _collect_pending(self) -> list[_PendingBatch]:
        events = self._task_event_store.list_events(state="routed")
        if not events:
            return []
        # Group by (owner, task_id). Routed events always carry a task_id; any
        # without one is not a manager concern and is skipped.
        grouped: dict[tuple[str, str], list[TaskEvent]] = {}
        for event in events:
            if event.task_id is None:
                continue
            owner = event.owner_user_id or "__anonymous__"
            grouped.setdefault((owner, event.task_id), []).append(event)
        batches: list[_PendingBatch] = []
        for (owner, task_id), task_events in grouped.items():
            key = AgentQueueKey(
                role=TASK_MANAGER_ROLE,
                owner_user_id=owner,
                scope_id=task_id,
            )
            claimed = self._store.list_claimed_source_ids(
                TASK_MANAGER_ROLE,
                owner,
                scope_id=task_id,
            )
            unclaimed = [e for e in task_events if e.id not in claimed]
            if unclaimed:
                batches.append(_PendingBatch(key=key, events=unclaimed))
        return batches

    def _is_idle(self, key: AgentQueueKey) -> bool:
        if key.scope_id is None:
            return False
        task = self._task_store.get(key.scope_id)
        if task is None or task.manager_conversation_id is None:
            return False
        return self._status_reader.status_for(task.manager_conversation_id) == "idle"

    def _flush(self, batch: _PendingBatch) -> AgentQueueItem | None:
        if batch.key.scope_id is None:
            return None
        task = self._task_store.get(batch.key.scope_id)
        if task is None or task.manager_conversation_id is None:
            _logger.debug(
                "manager packager: no live manager session for task %s; %d events stay routed",
                batch.key.scope_id,
                len(batch.events),
            )
            return None
        notice = _format_manager_notice(batch.events)
        return self._store.enqueue(
            uuid.uuid4().hex,
            batch.key,
            "notice",
            source_ids=[event.id for event in batch.events],
            payload=notice,
        )


# ── Status reader interface ─────────────────────────


class _StatusReader(ABC):
    """Reads the raw published status of a session."""

    @abstractmethod
    def status_for(self, session_id: str) -> str | None:
        """Return ``"idle"``/``"running"``/``"failed"``, or ``None`` on miss."""


# ── Module-level wiring (matches the secretary_queue.py pattern) ───

_secretary_packager: SecretaryPackager | None = None


def configure_secretary_packager(packager: SecretaryPackager | None) -> None:
    """Register or clear the global secretary packager."""
    global _secretary_packager
    _secretary_packager = packager


def get_secretary_packager() -> SecretaryPackager | None:
    """Return the configured secretary packager, if any."""
    return _secretary_packager
