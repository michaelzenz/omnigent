"""Durable fallback reconciliation for managed worker executions."""

from __future__ import annotations

import asyncio
import logging

from omnigent.agent_tasks.completion import notify_worker_execution_status
from omnigent.entities import MessageData, TaskEventExecution
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore

_logger = logging.getLogger(__name__)
_INTERVAL_S = 5.0


def _has_completed_response(
    execution: TaskEventExecution,
    conversation_store: ConversationStore,
) -> bool:
    if execution.conversation_id is None:
        return False
    page = conversation_store.list_items(
        execution.conversation_id,
        limit=500,
        order="desc",
    )
    items = list(reversed(page.data))
    instruction_index = next(
        (
            index
            for index, item in enumerate(items)
            if item.response_id == execution.id
            and item.type == "message"
            and isinstance(item.data, MessageData)
            and item.data.role == "user"
        ),
        None,
    )
    if instruction_index is None:
        return False
    return any(
        item.type == "message"
        and item.status == "completed"
        and isinstance(item.data, MessageData)
        and item.data.role == "assistant"
        for item in items[instruction_index + 1 :]
    )


async def reconcile_running_executions_once(
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
) -> int:
    """Complete running attempts whose durable worker transcript has settled."""
    executions = await asyncio.to_thread(
        task_event_store.list_executions_by_status,
        "running",
    )
    reconciled = 0
    for execution in executions:
        if execution.conversation_id is None:
            continue
        conversation = await asyncio.to_thread(
            conversation_store.get_conversation,
            execution.conversation_id,
        )
        if conversation is None:
            continue
        if conversation.live_status == "failed":
            handled = await notify_worker_execution_status(execution.id, "failed")
        elif conversation.live_status == "idle":
            has_response = await asyncio.to_thread(
                _has_completed_response,
                execution,
                conversation_store,
            )
            handled = (
                await notify_worker_execution_status(execution.id, "idle")
                if has_response
                else False
            )
        else:
            handled = False
        if handled:
            reconciled += 1
    return reconciled


async def run_execution_reconciler(
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    *,
    interval_s: float = _INTERVAL_S,
) -> None:
    """Reconcile running worker attempts until server shutdown."""
    while True:
        try:
            await reconcile_running_executions_once(
                task_event_store,
                conversation_store,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _logger.exception("worker execution reconciliation failed")
        await asyncio.sleep(interval_s)
