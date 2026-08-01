"""In-memory producer-consumer queue for batched secretary wakes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from omnigent.agent_tasks.constants import (
    AMBIGUOUS_EVENT_STATES,
    SECRETARY_BATCH_DEBOUNCE_SECONDS,
    SECRETARY_BATCH_MAX_SIZE,
)
from omnigent.agent_tasks.wake import wake_secretary_for_stalled_events
from omnigent.entities import TaskEvent
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_event_store import TaskEventStore

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecretaryQueueItem:
    """One stalled event waiting for a batched secretary wake."""

    event_id: str
    owner_user_id: str


@dataclass
class SecretaryQueueContext:
    """Stores required to run the secretary queue consumer."""

    task_event_store: TaskEventStore
    conversation_store: ConversationStore
    task_role_profile_store: TaskRoleProfileStore
    runner_router: RunnerRouter | None = None


_context: SecretaryQueueContext | None = None
_queue: asyncio.Queue[SecretaryQueueItem | None] | None = None
_consumer_task: asyncio.Task[None] | None = None


def configure_secretary_queue(context: SecretaryQueueContext | None) -> None:
    """Register or clear the global secretary queue context."""
    global _context
    _context = context


def get_secretary_queue_context() -> SecretaryQueueContext | None:
    """Return the configured secretary queue context."""
    return _context


async def enqueue_secretary_event(
    *,
    event_id: str,
    owner_user_id: str,
) -> None:
    """Enqueue a stalled event for batched secretary processing."""
    if _queue is None:
        _logger.warning(
            "secretary queue not started; event %s left in awaiting_grouping",
            event_id,
        )
        return
    await _queue.put(SecretaryQueueItem(event_id=event_id, owner_user_id=owner_user_id))


async def start_secretary_consumer() -> None:
    """Start the background secretary queue consumer."""
    global _queue, _consumer_task
    if _consumer_task is not None and not _consumer_task.done():
        return
    _queue = asyncio.Queue()
    _consumer_task = asyncio.create_task(_consumer_loop(), name="secretary-queue")


async def stop_secretary_consumer() -> None:
    """Stop the background secretary queue consumer."""
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
        deadline = loop.time() + SECRETARY_BATCH_DEBOUNCE_SECONDS
        while len(batch) < SECRETARY_BATCH_MAX_SIZE:
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
        by_user: dict[str, list[SecretaryQueueItem]] = {}
        for item in batch:
            by_user.setdefault(item.owner_user_id, []).append(item)
        for user_id, items in by_user.items():
            await _process_user_batch(user_id, items)


async def _process_user_batch(user_id: str, items: list[SecretaryQueueItem]) -> None:
    if _context is None:
        return
    events: list[TaskEvent] = []
    seen_event_ids: set[str] = set()
    for item in items:
        if item.event_id in seen_event_ids:
            continue
        event = _context.task_event_store.get_event(item.event_id)
        if event is None or event.state not in AMBIGUOUS_EVENT_STATES:
            continue
        seen_event_ids.add(item.event_id)
        events.append(event)
    if not events:
        return
    await wake_secretary_for_stalled_events(
        user_id=user_id,
        events=events,
        task_role_profile_store=_context.task_role_profile_store,
        conversation_store=_context.conversation_store,
        runner_router=_context.runner_router,
    )


async def flush_secretary_queue_for_tests() -> None:
    """Drain the queue and process pending batches (tests only)."""
    if _queue is None:
        return
    pending: list[SecretaryQueueItem] = []
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
    by_user: dict[str, list[SecretaryQueueItem]] = {}
    for item in pending:
        by_user.setdefault(item.owner_user_id, []).append(item)
    for user_id, items in by_user.items():
        await _process_user_batch(user_id, items)
