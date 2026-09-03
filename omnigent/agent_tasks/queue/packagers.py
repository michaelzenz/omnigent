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
from typing import Any

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    TASK_MANAGER_ROLE,
)
from omnigent.agent_tasks.broker_inbox import (
    AmbiguousEventCluster,
    cluster_events_by_similarity,
)
from omnigent.agent_tasks.bootstrap import bootstrap_task_manager, resolve_bootstrap_params
from omnigent.agent_tasks.broker_session import ensure_broker_session, get_or_create_role_profile
from omnigent.agent_tasks.manager_role_profile import load_manager_role_profile
from omnigent.agent_tasks.constants import (
    BROKER_BATCH_MAX_SIZE,
    BROKER_CANDIDATE_LIMIT,
    BROKER_TAG_SIMILARITY_THRESHOLD,
    MANAGER_BATCH_MAX_SIZE,
)
from omnigent.agent_tasks.event_host import event_host
from omnigent.agent_tasks.event_types import (
    EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
    EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
    SESSION_TURN_FINISHED_EVENT_TYPE,
)
from omnigent.agent_tasks.notices import _format_broker_stall_notice, _format_manager_notice
from omnigent.agent_tasks.task_match import rank_tasks_for_events, routable_tasks
from omnigent.db.utils import now_epoch
from omnigent.entities import AgentQueueItem, AgentQueueKey, Task, TaskEvent
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.prompt_profile_store import PromptProfileStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.user_role_session_store import UserRoleSessionStore


def _is_session_event(event_type: str) -> bool:
    """Whether this event is a session-watcher event subject to cooldown + per-session grouping."""
    return event_type.startswith("session.") or event_type == EXTERNAL_SESSION_UPDATED_EVENT_TYPE

_logger = logging.getLogger(__name__)

# ── Configurable defaults (global constants for now) ───

# How often each packager scans business state.
DEFAULT_PACKAGER_POLL_INTERVAL_S = 5.0

# Minimum age before a partial batch is flushed to a ready-but-not-full agent.
# Unified with session event cooldown — all events wait the same window.
DEFAULT_PACKAGER_AGE_THRESHOLD_S = 180

# ── Base class ───────────────────────────────────────


@dataclass
class _PendingBatch:
    """One key's accumulated pending signals within a scan pass."""

    key: AgentQueueKey
    events: list[TaskEvent] = field(default_factory=list)
    # Broker-only metadata carried through to ``_flush`` so the notice can
    # carry the included clusters / orphan flag. For a routed batch this holds
    # the clusters packed into this notice (each possibly capped to ``batch_size``);
    # ``events`` is the flat union of those clusters' events. Other roles leave
    # these at their defaults.
    clusters: list[AmbiguousEventCluster] | None = None
    is_orphan: bool = False
    # Manager-only: task_id → title for the batch's events, so the notice can
    # label each event with its task when one manager spans several tasks.
    task_titles: dict[str, str] | None = None
    # Manager-only: task_id → state for every task on this manager session,
    # for the roster footer (not just the batch's tasks).
    task_states: dict[str, str] | None = None

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
        """The role this packager feeds (e.g. ``"broker"``)."""

    @abstractmethod
    def _collect_pending(self) -> list[_PendingBatch]:
        """Scan business state, return pending signals grouped by key.

        Excludes signals already claimed by an open queue item.
        """

    @abstractmethod
    async def _is_idle(self, key: AgentQueueKey) -> bool:
        """Is the destination agent for *key* currently idle (raw status)?

        Returns ``False`` when there is no live agent session for the key, so a
        missing agent never triggers a partial-batch flush.
        """

    @abstractmethod
    async def _flush(self, batch: _PendingBatch) -> AgentQueueItem | None:
        """Format the payload and enqueue one item. ``None`` if unpackageable."""

    async def _should_send(self, batch: _PendingBatch) -> bool:
        """Evaluate the batching matrix for one key.

        Full batches flush immediately — never hold completed work. Partial
        batches flush early when the agent is idle so it doesn't sit waiting
        for the packager; but when the agent is already busy, we hold the
        partial batch to accumulate more events, since the agent can't
        process them yet anyway. The dispatcher's gate is the final
        authority on delivery timing.
        """
        if not batch.events:
            return False
        if len(batch.events) >= self._batch_size:
            return True
        # Partial batch: flush early only when the agent is idle and ready.
        if not await self._is_idle(batch.key):
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
            if await self._should_send(batch):
                try:
                    await self._flush(batch)
                except Exception:
                    _logger.exception(
                        "packager %s failed to flush %s",
                        self.role,
                        batch.key,
                    )

    async def scan_once(self) -> None:
        """Run one scan (tests can await this directly)."""
        for batch in self._collect_pending():
            if await self._should_send(batch):
                await self._flush(batch)


# ── Broker packager ────────────────────────────────


class BrokerPackager(Packager):
    """Stage-1 packager for broker events.

    Scans ``awaiting_grouping`` events each tick, groups by (owner, host) —
    events from different hosts never share a batch, since each batch must be
    distributable to a host-compatible manager — excludes already-claimed
    events, and for each group splits watcher-discovered external sessions
    (one notice each) from routed business events (clustered by tag
    similarity, oldest-first). The agent-idle check reads the broker's bound
    session from the role profile and looks up its raw status. The broker has
    no UI surface to boot its own session, so when the conversation/agent/
    host stores are wired the packager provisions one on demand.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        task_event_store: TaskEventStore,
        task_role_profile_store: TaskRoleProfileStore,
        user_role_session_store: UserRoleSessionStore,
        task_store: TaskStore,
        status_reader: _StatusReader,
        *,
        conversation_store: ConversationStore | None = None,
        agent_store: AgentStore | None = None,
        host_store: HostStore | None = None,
        prompt_profile_store: PromptProfileStore | None = None,
        session_creator: Any | None = None,
        app_state: Any = None,
        poll_interval_s: float = DEFAULT_PACKAGER_POLL_INTERVAL_S,
        batch_size: int = BROKER_BATCH_MAX_SIZE,
        age_threshold_s: float = DEFAULT_PACKAGER_AGE_THRESHOLD_S,
        similarity_threshold: float = BROKER_TAG_SIMILARITY_THRESHOLD,
        candidate_limit: int = BROKER_CANDIDATE_LIMIT,
    ) -> None:
        super().__init__(
            store,
            poll_interval_s=poll_interval_s,
            batch_size=batch_size,
            age_threshold_s=age_threshold_s,
        )
        self._task_event_store = task_event_store
        self._task_role_profile_store = task_role_profile_store
        self._user_role_session_store = user_role_session_store
        self._task_store = task_store
        self._status_reader = status_reader
        self._conversation_store = conversation_store
        self._agent_store = agent_store
        self._host_store = host_store
        self._prompt_profile_store = prompt_profile_store
        self._session_creator = session_creator
        self._app_state = app_state
        self._similarity_threshold = similarity_threshold
        self._candidate_limit = candidate_limit

    async def _broker_conversation_id(self, owner_user_id: str) -> str | None:
        """Return the owner's broker conversation id without side effects."""
        session = self._user_role_session_store.get(owner_user_id, TASK_BROKER_ROLE)
        if session is None:
            return None
        return session.conversation_id

    async def _live_broker_conversation_id(self, owner_user_id: str) -> str | None:
        """Return the owner's live broker conversation, booting one if needed."""
        if (
            self._conversation_store is not None
            and self._agent_store is not None
            and self._host_store is not None
        ):
            try:
                return await ensure_broker_session(
                    owner_user_id=owner_user_id,
                    task_role_profile_store=self._task_role_profile_store,
                    user_role_session_store=self._user_role_session_store,
                    conversation_store=self._conversation_store,
                    agent_store=self._agent_store,
                    host_store=self._host_store,
                    session_creator=self._session_creator,
                    app_state=self._app_state,
                    prompt_profile_store=self._prompt_profile_store,
                )
            except Exception:
                # One owner's bootstrap must not abort the scan for the rest.
                _logger.exception("broker packager: failed to boot broker for %s", owner_user_id)
                return None
        session = self._user_role_session_store.get(owner_user_id, TASK_BROKER_ROLE)
        if session is None:
            return None
        return session.conversation_id

    @property
    def role(self) -> str:
        return TASK_BROKER_ROLE

    def _collect_pending(self) -> list[_PendingBatch]:
        events = self._task_event_store.list_events(state="awaiting_grouping")
        if not events:
            return []
        # Group by (owner, host); events without an owner fall back to
        # "__anonymous__", events without host attribution to None. Hosts never
        # mix inside one batch — each batch must be distributable to a
        # host-compatible manager.
        grouped: dict[tuple[str, str | None], list[TaskEvent]] = {}
        for event in events:
            owner = event.owner_user_id or "__anonymous__"
            grouped.setdefault((owner, event_host(event)), []).append(event)
        batches: list[_PendingBatch] = []
        for (owner, _host), owner_events in grouped.items():
            key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id=owner)
            claimed = self._store.list_claimed_source_ids(
                TASK_BROKER_ROLE,
                owner,
            )
            unclaimed = [e for e in owner_events if e.id not in claimed]
            if not unclaimed:
                continue
            discovered = [
                e for e in unclaimed if e.event_type == EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE
            ]
            routed = [
                e for e in unclaimed if e.event_type != EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE
            ]
            # Each discovered external session is its own batch — the broker
            # reads the transcript snippet and decides: adopt, create task, or FYI.
            for disc in discovered:
                batches.append(_PendingBatch(key=key, events=[disc], is_orphan=True))
            # Routed events: cluster by tag similarity, then fill ONE notice per
            # poll up to ``batch_size``. Clusters are taken oldest-first; a cluster
            # that would overflow the remaining capacity is capped to its oldest
            # ``remaining`` events (the rest stay ``awaiting_grouping`` and are
            # re-clustered next poll). Similar events stay contiguous per cluster.
            clusters = cluster_events_by_similarity(routed, threshold=self._similarity_threshold)
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

    async def _is_idle(self, key: AgentQueueKey) -> bool:
        conversation_id = await self._broker_conversation_id(key.owner_user_id)
        if conversation_id is None:
            return False
        status = self._status_reader.status_for(conversation_id)
        # None = no status reported yet (cold cache after restart). Treat as
        # idle so events flush to the dispatcher, which will retry delivery.
        return status is None or status == "idle"

    async def _flush(self, batch: _PendingBatch) -> AgentQueueItem | None:
        # Read-only lookup: the dispatcher's deliver path handles runner
        # resolution and retry. Booting a runner here blocks the packager
        # poll loop (30s timeout) and wedges all owners behind one slow boot.
        conversation_id = await self._broker_conversation_id(batch.key.owner_user_id)
        if conversation_id is None:
            # No session at all — try a full boot on first flush only.
            conversation_id = await self._live_broker_conversation_id(batch.key.owner_user_id)
        if conversation_id is None:
            _logger.debug(
                "broker packager: no broker session for %s; %d events stay in awaiting_grouping",
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
        notice = _format_broker_stall_notice(
            batch.events,
            clusters=batch.clusters,
            candidate_task_ids=candidate_task_ids,
            is_orphan=batch.is_orphan,
        )
        item = self._store.enqueue(
            uuid.uuid4().hex,
            batch.key,
            "notice",
            source_ids=[event.id for event in batch.events],
            payload=notice,
        )
        if item is not None:
            for event in batch.events:
                self._task_event_store.update_event(event.id, state="pending_triage")
        return item


# ── Manager packager ────────────────────────────────


class ManagerPackager(Packager):
    """Stage-1 packager for routed task events.

    Scans ``routed`` events each tick — these are events the ingress scorer (or the
    broker resolve path) already bound to a task, plus ``worker.execution.finished``
    events the completion hook emits when a worker settles. They are grouped by
    ``(owner, manager_conversation_id)`` — one queue per manager session, shared
    by every task bound to that manager — and evaluated against the same batching
    matrix as the broker. The agent-idle check reads the manager session status
    directly. When a task has no manager session yet, the packager bootstraps one
    on demand (mirroring the broker pattern) so routed events are never stranded.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        task_event_store: TaskEventStore,
        task_store: TaskStore,
        status_reader: _StatusReader,
        *,
        task_role_profile_store: TaskRoleProfileStore | None = None,
        conversation_store: ConversationStore | None = None,
        agent_store: AgentStore | None = None,
        host_store: HostStore | None = None,
        prompt_profile_store: PromptProfileStore | None = None,
        session_creator: Any | None = None,
        app_state: Any | None = None,
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
        self._task_role_profile_store = task_role_profile_store
        self._conversation_store = conversation_store
        self._agent_store = agent_store
        self._host_store = host_store
        self._prompt_profile_store = prompt_profile_store
        self._session_creator = session_creator
        self._app_state = app_state

    @property
    def role(self) -> str:
        return TASK_MANAGER_ROLE

    async def _ensure_manager_for_task(self, task: Task, owner: str) -> str | None:
        """Bootstrap a manager session for a task that has none.

        Mirrors the broker's ``_live_broker_conversation_id``: when the
        packager's stores are wired, call ``bootstrap_task_manager`` to
        attach-or-create. Returns the manager conversation id, or ``None``
        when bootstrap is unavailable (no stores) or fails — in that case
        the task's events stay ``routed`` and are retried next poll.
        """
        if (
            self._task_role_profile_store is None
            or self._conversation_store is None
            or self._session_creator is None
            or self._app_state is None
        ):
            return None
        auth_user_id = None if owner == "__anonymous__" else owner
        role_profile = await asyncio.to_thread(
            load_manager_role_profile,
            self._task_role_profile_store,
            task,
        )
        if role_profile is None and self._host_store is not None and self._agent_store is not None:
            try:
                role_profile = await asyncio.to_thread(
                    get_or_create_role_profile,
                    role=task.manager_role_key,
                    auth_user_id=auth_user_id,
                    task_role_profile_store=self._task_role_profile_store,
                    host_store=self._host_store,
                    agent_store=self._agent_store,
                    prompt_profile_store=self._prompt_profile_store,
                )
            except Exception:
                _logger.exception(
                    "manager packager: failed to provision role for task %s",
                    task.id,
                )
                return None
        if role_profile is None:
            return None
        params = resolve_bootstrap_params(
            host_id=role_profile.host_id,
            workspace=role_profile.workspace,
            harness=role_profile.harness,
            model=role_profile.model,
            role_profile=role_profile,
        )
        try:
            updated = await bootstrap_task_manager(
                task=task,
                task_store=self._task_store,
                conversation_store=self._conversation_store,
                params=params,
                session_creator=self._session_creator,
                app_state=self._app_state,
                user_id=auth_user_id,
            )
            return updated.manager_conversation_id
        except Exception:
            _logger.exception(
                "manager packager: failed to bootstrap manager for task %s",
                task.id,
            )
            return None

    def _collect_pending(self) -> list[_PendingBatch]:
        events = self._task_event_store.list_events(state="routed")
        if not events:
            return []
        # Resolve each event's task to its manager session. Tasks without one
        # are collected for bootstrap so their events are not stranded.
        manager_by_task: dict[str, str] = {}
        title_by_task: dict[str, str] = {}
        needs_bootstrap: set[str] = set()
        for task_id in {event.task_id for event in events if event.task_id is not None}:
            task = self._task_store.get(task_id)
            if task is None:
                continue
            if task.manager_conversation_id is None:
                needs_bootstrap.add(task_id)
                continue
            manager_by_task[task_id] = task.manager_conversation_id
            title_by_task[task_id] = task.title
        # Bootstrap tasks with no manager session. This is async but
        # ``_collect_pending`` is sync (called from the sync ``_scan_once``
        # context via ``self._collect_pending()``). We schedule the bootstrap
        # and let it run; next poll will pick up the events once the task
        # has a manager_conversation_id.
        if needs_bootstrap:
            asyncio.ensure_future(
                self._bootstrap_pending_tasks(needs_bootstrap)
            )
        # Group by (owner, manager session). Routed events always carry a
        # task_id; any without one is not a manager concern and is skipped.
        grouped: dict[tuple[str, str], list[TaskEvent]] = {}
        for event in events:
            if event.task_id is None:
                continue
            manager_conv_id = manager_by_task.get(event.task_id)
            if manager_conv_id is None:
                continue
            owner = event.owner_user_id or "__anonymous__"
            grouped.setdefault((owner, manager_conv_id), []).append(event)
        batches: list[_PendingBatch] = []
        now = now_epoch()
        for (owner, manager_conv_id), task_events in grouped.items():
            key = AgentQueueKey(
                role=TASK_MANAGER_ROLE,
                owner_user_id=owner,
                scope_id=manager_conv_id,
            )
            claimed = self._store.list_claimed_source_ids(
                TASK_MANAGER_ROLE,
                owner,
                scope_id=manager_conv_id,
            )
            unclaimed = [e for e in task_events if e.id not in claimed]
            if not unclaimed:
                continue
            task_titles = {
                e.task_id: title_by_task[e.task_id]
                for e in unclaimed
                if e.task_id is not None and e.task_id in title_by_task
            }
            task_states = {
                task.id: task.state
                for task in self._task_store.list_by_manager_conversation_id(manager_conv_id)
            }
            # Split session events (cooldown + per-session grouping) from
            # other routed events (existing single-batch behavior).
            session_events: list[TaskEvent] = []
            other_events: list[TaskEvent] = []
            for event in unclaimed:
                if _is_session_event(event.event_type):
                    if now - event.created_at < self._age_threshold_s:
                        continue  # too young — leave it routed for next poll
                    session_events.append(event)
                else:
                    other_events.append(event)
            # Non-session events: one batch (existing behavior).
            if other_events:
                batches.append(
                    _PendingBatch(
                        key=key,
                        events=other_events,
                        task_titles=task_titles,
                        task_states=task_states,
                    )
                )
            # Session events: one batch per source_key (per session), so all
            # events for the same session arrive in one notice.
            by_session: dict[str, list[TaskEvent]] = {}
            for event in session_events:
                sk = event.source_key or event.id
                by_session.setdefault(sk, []).append(event)
            for session_evts in by_session.values():
                batches.append(
                    _PendingBatch(
                        key=key,
                        events=session_evts,
                        task_titles=task_titles,
                        task_states=task_states,
                    )
                )
        return batches

    async def _bootstrap_pending_tasks(self, task_ids: set[str]) -> None:
        """Bootstrap manager sessions for tasks that have none.

        Called from ``_collect_pending`` when routed events are stranded on
        tasks without a manager session. Each task is bootstrapped
        independently — one failure does not block the rest. On success the
        task's ``manager_conversation_id`` is set, and the next poll will
        group its events normally.
        """
        for task_id in task_ids:
            task = await asyncio.to_thread(self._task_store.get, task_id)
            if task is None or task.manager_conversation_id is not None:
                continue
            owner = task.owner_user_id or "__anonymous__"
            await self._ensure_manager_for_task(task, owner)

    async def _is_idle(self, key: AgentQueueKey) -> bool:
        if key.scope_id is None:
            return False
        return self._status_reader.status_for(key.scope_id) == "idle"

    async def _flush(self, batch: _PendingBatch) -> AgentQueueItem | None:
        if batch.key.scope_id is None:
            return None
        notice = _format_manager_notice(
            batch.events,
            task_titles=batch.task_titles,
            task_states=batch.task_states,
        )
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


# ── Module-level wiring (matches the broker_queue pattern) ───

_broker_packager: BrokerPackager | None = None


def configure_broker_packager(packager: BrokerPackager | None) -> None:
    """Register or clear the global broker packager."""
    global _broker_packager
    _broker_packager = packager


def get_broker_packager() -> BrokerPackager | None:
    """Return the configured broker packager, if any."""
    return _broker_packager
