"""In-memory producer-consumer queue for task-distributor batches."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from omnigent.agent_tasks.constants import (
    DISTRIBUTOR_BATCH_DEBOUNCE_SECONDS,
    DISTRIBUTOR_BATCH_MAX_SIZE,
    DISTRIBUTOR_ESCALATION_SECONDS,
    ORPHAN_EVENT_STATES,
    distributor_agent_enabled,
)
from omnigent.agent_tasks.distributor_session import ensure_distributor_conversation
from omnigent.agent_tasks.wake import (
    wake_distributor_for_batch,
    wake_secretary_for_stalled_events,
)
from omnigent.entities import Task, TaskEvent
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.secretary_profile_store import SecretaryProfileStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedCandidateSnapshot:
    """Serializable routing candidate for queue items."""

    task_id: str
    title: str
    score: float


@dataclass(frozen=True)
class DistributorQueueItem:
    """One stalled event waiting for distributor batch processing."""

    event_id: str
    owner_user_id: str
    ranked_candidates: tuple[RankedCandidateSnapshot, ...]


@dataclass
class DistributorQueueContext:
    """Stores required to run the distributor queue consumer."""

    task_store: TaskStore
    task_event_store: TaskEventStore
    conversation_store: ConversationStore
    agent_store: AgentStore
    secretary_profile_store: SecretaryProfileStore
    runner_router: RunnerRouter | None = None


_context: DistributorQueueContext | None = None
_queue: asyncio.Queue[DistributorQueueItem | None] | None = None
_consumer_task: asyncio.Task[None] | None = None


def configure_distributor_queue(context: DistributorQueueContext | None) -> None:
    """Register or clear the global distributor queue context."""
    global _context
    _context = context


def get_distributor_queue_context() -> DistributorQueueContext | None:
    """Return the configured distributor queue context."""
    return _context


def _snapshot_ranked(ranked: list[tuple[Task, float]]) -> tuple[RankedCandidateSnapshot, ...]:
    return tuple(
        RankedCandidateSnapshot(task_id=task.id, title=task.title, score=score)
        for task, score in ranked
    )


def _ranked_from_snapshots(
    snapshots: tuple[RankedCandidateSnapshot, ...],
    *,
    task_store: TaskStore,
) -> list[tuple[Task, float]]:
    ranked: list[tuple[Task, float]] = []
    for row in snapshots:
        task = task_store.get(row.task_id)
        if task is None:
            continue
        ranked.append((task, row.score))
    return ranked


async def enqueue_distributor_event(
    *,
    event_id: str,
    owner_user_id: str,
    ranked: list[tuple[Task, float]],
) -> None:
    """Enqueue a stalled event for distributor batch processing."""
    if not distributor_agent_enabled():
        return
    if _queue is None:
        _logger.warning(
            "distributor queue not started; event %s left in awaiting_grouping",
            event_id,
        )
        return
    item = DistributorQueueItem(
        event_id=event_id,
        owner_user_id=owner_user_id,
        ranked_candidates=_snapshot_ranked(ranked),
    )
    await _queue.put(item)


async def start_distributor_consumer() -> None:
    """Start the background distributor queue consumer."""
    global _queue, _consumer_task
    if _consumer_task is not None and not _consumer_task.done():
        return
    _queue = asyncio.Queue()
    _consumer_task = asyncio.create_task(_consumer_loop(), name="distributor-queue")


async def stop_distributor_consumer() -> None:
    """Stop the background distributor queue consumer."""
    global _queue, _consumer_task
    if _queue is not None:
        await _queue.put(None)
    if _consumer_task is not None:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
    _consumer_task = None
    _queue = None


async def _consumer_loop() -> None:
    assert _queue is not None
    while True:
        first = await _queue.get()
        if first is None:
            break
        batch = [first]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DISTRIBUTOR_BATCH_DEBOUNCE_SECONDS
        while len(batch) < DISTRIBUTOR_BATCH_MAX_SIZE:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(_queue.get(), timeout=remaining)
            except TimeoutError:
                break
            if item is None:
                await _queue.put(None)
                break
            batch.append(item)
        by_user: dict[str, list[DistributorQueueItem]] = {}
        for item in batch:
            by_user.setdefault(item.owner_user_id, []).append(item)
        for user_id, items in by_user.items():
            await _process_user_batch(user_id, items)


async def _process_user_batch(user_id: str, items: list[DistributorQueueItem]) -> None:
    if _context is None:
        return
    events: list[TaskEvent] = []
    ranked_by_event: dict[str, list[tuple[Task, float]]] = {}
    for item in items:
        event = _context.task_event_store.get_event(item.event_id)
        if event is None or event.state not in ORPHAN_EVENT_STATES:
            continue
        events.append(event)
        ranked_by_event[event.id] = _ranked_from_snapshots(
            item.ranked_candidates,
            task_store=_context.task_store,
        )
    if not events:
        return

    conversation_id = ensure_distributor_conversation(
        user_id=user_id,
        conversation_store=_context.conversation_store,
        agent_store=_context.agent_store,
        secretary_profile_store=_context.secretary_profile_store,
    )
    if conversation_id is None:
        _logger.warning(
            "distributor batch skipped: no session for user %s (%s event(s))",
            user_id,
            len(events),
        )
        await wake_secretary_for_stalled_events(
            user_id=user_id,
            events=events,
            ranked_candidates=ranked_by_event,
            secretary_profile_store=_context.secretary_profile_store,
            conversation_store=_context.conversation_store,
            runner_router=_context.runner_router,
        )
        return

    await wake_distributor_for_batch(
        distributor_conversation_id=conversation_id,
        events=events,
        ranked_candidates=ranked_by_event,
        conversation_store=_context.conversation_store,
        runner_router=_context.runner_router,
    )
    event_ids = [event.id for event in events]
    asyncio.create_task(
        _escalate_unresolved_after_delay(
            user_id=user_id,
            event_ids=event_ids,
            ranked_by_event=ranked_by_event,
        ),
        name=f"distributor-escalate-{user_id}",
    )


async def _escalate_unresolved_after_delay(
    *,
    user_id: str,
    event_ids: list[str],
    ranked_by_event: dict[str, list[tuple[Task, float]]],
) -> None:
    await asyncio.sleep(DISTRIBUTOR_ESCALATION_SECONDS)
    if _context is None:
        return
    unresolved: list[TaskEvent] = []
    ranked: dict[str, list[tuple[Task, float]]] = {}
    for event_id in event_ids:
        event = _context.task_event_store.get_event(event_id)
        if event is None or event.state not in ORPHAN_EVENT_STATES:
            continue
        unresolved.append(event)
        ranked[event.id] = ranked_by_event.get(event.id, [])
    if not unresolved:
        return
    await wake_secretary_for_stalled_events(
        user_id=user_id,
        events=unresolved,
        ranked_candidates=ranked,
        secretary_profile_store=_context.secretary_profile_store,
        conversation_store=_context.conversation_store,
        runner_router=_context.runner_router,
    )


async def flush_distributor_queue_for_tests() -> None:
    """Drain the queue and process pending batches (tests only)."""
    if _queue is None:
        return
    pending: list[DistributorQueueItem] = []
    while True:
        try:
            item = _queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if item is None:
            break
        pending.append(item)
    if not pending:
        return
    by_user: dict[str, list[DistributorQueueItem]] = {}
    for item in pending:
        by_user.setdefault(item.owner_user_id, []).append(item)
    for user_id, items in by_user.items():
        await _process_user_batch(user_id, items)
