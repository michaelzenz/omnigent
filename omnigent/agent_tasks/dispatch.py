"""Dispatch task workers against TaskItem backlog units."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.executions import mark_execution_running, start_execution_for_item
from omnigent.agent_tasks.task_activity import sync_task_activity_state
from omnigent.agent_tasks.workers import worker_for_item
from omnigent.entities import (
    MessageData,
    NewConversationItem,
    Task,
    TaskEventExecution,
    TaskItem,
)
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore


@dataclass(frozen=True)
class DispatchParams:
    """Resolved instructions for one Worker turn."""

    instructions: str


def parse_dispatch_payload(payload: str | None) -> dict[str, Any]:
    """Parse a JSON dispatch payload."""
    if payload is None or not payload.strip():
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OmnigentError("payload must be valid JSON", code=ErrorCode.INVALID_INPUT) from exc
    if not isinstance(parsed, dict):
        raise OmnigentError("payload must be a JSON object", code=ErrorCode.INVALID_INPUT)
    return parsed


def compose_worker_instructions(
    *,
    instructions: str | None,
    internal_note: str | None,
) -> str:
    """Merge worker instructions with agent-facing context."""
    worker_text = (instructions or "").strip()
    note = (internal_note or "").strip()
    if note and worker_text:
        return f"{worker_text}\n\n## Context\n{note}"
    if note:
        return note
    return worker_text


def resolve_dispatch_params(
    *,
    payload: dict[str, Any],
    instructions: str | None = None,
    host_id: str | None = None,
    workspace: str | None = None,
    harness: str | None = None,
    model: str | None = None,
    role_profile: TaskRoleProfile | None = None,
) -> DispatchParams:
    """Resolve the instructions sent to an already initialized Worker."""
    _ = (host_id, workspace, harness, model, role_profile)
    resolved_instructions = compose_worker_instructions(
        instructions=instructions if instructions is not None else payload.get("instructions"),
        internal_note=payload.get("internal_note"),
    )
    if not resolved_instructions:
        raise OmnigentError(
            "instructions are required",
            code=ErrorCode.INVALID_INPUT,
        )
    return DispatchParams(instructions=resolved_instructions)


def _worker_instruction_item(instructions: str, response_id: str) -> NewConversationItem:
    """Build the visible user message that starts one worker turn."""
    return NewConversationItem(
        type="message",
        response_id=response_id,
        data=MessageData(
            role="user",
            content=[{"type": "input_text", "text": instructions}],
            is_meta=False,
        ),
    )


async def dispatch_worker_for_item(
    *,
    task: Task,
    item: TaskItem,
    params: DispatchParams,
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    session_creator: Any | None = None,
    app_state: Any | None = None,
    user_id: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[TaskEventExecution, str]:
    """Dispatch one task item to an initialized Worker target.

    Initialization owns target creation. Every dispatch reuses ``target_id``
    and appends the instructions as a real user message.
    """
    _ = (session_creator, app_state, user_id)
    if task.manager_conversation_id is None:
        raise OmnigentError(
            "Task manager is not bootstrapped",
            code=ErrorCode.CONFLICT,
        )
    if item.task_id != task.id:
        raise OmnigentError("Task item does not belong to task", code=ErrorCode.INVALID_INPUT)
    manager_conv = await asyncio.to_thread(
        conversation_store.get_conversation,
        task.manager_conversation_id,
    )
    if manager_conv is None:
        raise OmnigentError(
            "Manager session is missing",
            code=ErrorCode.CONFLICT,
        )

    if manager_conv.agent_id is None:
        raise OmnigentError(
            "Manager session has no agent binding",
            code=ErrorCode.CONFLICT,
        )

    worker = worker_for_item(item, worker_store=worker_store)
    if worker is None:
        raise OmnigentError(
            "Item has no worker lane; assign one before dispatching",
            code=ErrorCode.CONFLICT,
        )

    worker_conv_id = worker.target_id
    if worker.state != "idle" or worker_conv_id is None:
        raise OmnigentError(
            "Worker must be initialized and idle before dispatch",
            code=ErrorCode.CONFLICT,
        )
    existing = await asyncio.to_thread(
        conversation_store.get_conversation,
        worker_conv_id,
    )
    if existing is None:
        raise OmnigentError(
            "Worker target session is unavailable",
            code=ErrorCode.CONFLICT,
        )

    execution = (
        await asyncio.to_thread(
            task_event_store.get_execution_by_agent_queue_item_id,
            idempotency_key,
        )
        if idempotency_key is not None
        else None
    )
    if execution is None:
        execution = start_execution_for_item(
            task=task,
            item=item,
            task_event_store=task_event_store,
            agent_queue_item_id=idempotency_key,
            conversation_id=worker_conv_id,
            status="queued",
        )

    # Persist the attempt before exposing its instruction. A fast worker can
    # otherwise settle before the completion hook has an execution to update.
    message_item = _worker_instruction_item(params.instructions, execution.id)
    already_sent = False
    if execution.agent_queue_item_id is not None:
        recent = await asyncio.to_thread(
            conversation_store.list_items,
            worker_conv_id,
            limit=100,
            order="desc",
        )
        already_sent = any(item.response_id == execution.id for item in recent.data)
    if not already_sent:
        persisted = await asyncio.to_thread(
            conversation_store.append,
            worker_conv_id,
            [message_item],
        )
        if persisted:
            # Direct worker dispatch bypasses POST /sessions/{id}/events, so
            # publish the accepted input explicitly for an already-open chat.
            from omnigent.server.routes.sessions import _publish_input_consumed

            _publish_input_consumed(worker_conv_id, persisted[0])
    await asyncio.to_thread(
        worker_store.update_worker,
        worker.id,
        state="busy",
        needs_response=False,
    )

    if execution.status == "queued":
        marked = mark_execution_running(
            task_event_store,
            execution.id,
            conversation_id=worker_conv_id,
        )
        if marked is not None:
            execution = marked
    await asyncio.to_thread(task_item_store.update_item, item.id, state="running")
    sync_task_activity_state(
        task,
        task_store=task_store,
        task_item_store=task_item_store,
    )
    return execution, worker_conv_id
