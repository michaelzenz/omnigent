"""Dispatch task workers against TaskItem backlog units."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.agent_tasks.executions import mark_execution_running, start_execution_for_item
from omnigent.agent_tasks.task_activity import sync_task_activity_state
from omnigent.agent_tasks.workers import worker_for_item
from omnigent.entities import Task, TaskEventExecution, TaskItem
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore


@dataclass(frozen=True)
class DispatchParams:
    """Resolved worker dispatch inputs."""

    role_key: str
    agent_profile_id: str
    instructions: str
    host_id: str
    workspace: str
    harness: str
    model: str | None


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
    """Merge explicit dispatch fields over the payload and the lane's role."""
    resolved_instructions = compose_worker_instructions(
        instructions=instructions if instructions is not None else payload.get("instructions"),
        internal_note=payload.get("internal_note"),
    )
    if role_profile is None:
        raise OmnigentError(
            "a worker role is required to dispatch",
            code=ErrorCode.INVALID_INPUT,
        )
    if not resolved_instructions:
        raise OmnigentError(
            "instructions are required",
            code=ErrorCode.INVALID_INPUT,
        )
    bootstrap = resolve_bootstrap_params(
        host_id=host_id or payload.get("host_id"),
        workspace=workspace or payload.get("workspace"),
        harness=harness or payload.get("harness"),
        model=model or payload.get("model"),
        role_profile=role_profile,
    )
    return DispatchParams(
        role_key=role_profile.role,
        agent_profile_id=bootstrap.agent_profile_id,
        instructions=str(resolved_instructions),
        host_id=bootstrap.host_id,
        workspace=bootstrap.workspace,
        harness=bootstrap.harness,
        model=bootstrap.model,
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
) -> tuple[TaskEventExecution, str]:
    """Dispatch one task item to a worker, reusing or creating its session.

    The worker session is long-lived: the first dispatch creates it via
    ``session_creator`` (the ``POST /v1/sessions`` path); subsequent
    dispatches reuse the existing conversation and append the instructions
    as a real user message.
    """
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

    # Reuse the worker's existing session, or create one on first dispatch.
    worker_conv_id = worker.session_id
    if worker_conv_id is not None:
        existing = await asyncio.to_thread(
            conversation_store.get_conversation,
            worker_conv_id,
        )
        if existing is None:
            worker_conv_id = None

    if worker_conv_id is None:
        if session_creator is None or app_state is None:
            raise OmnigentError(
                "session_creator and app_state are required to create a worker session",
                code=ErrorCode.INVALID_INPUT,
            )
        from omnigent.agent_tasks.bootstrap import build_role_session_request
        from omnigent.server.routes.sessions import _make_internal_request

        body = build_role_session_request(
            _worker_profile_from_params(params, task),
            title=params.role_key,
            parent_session_id=task.manager_conversation_id,
            sub_agent_name=params.role_key,
        )
        request = _make_internal_request(app_state)
        resp = await session_creator(
            body=body,
            request=request,
            user_id=user_id,
        )
        worker_conv_id = resp.id
        await asyncio.to_thread(
            worker_store.update_worker,
            worker.id,
            session_id=worker_conv_id,
        )

    # Send the instructions as a real user message (not meta) so the worker
    # picks them up as its next turn.
    from omnigent.db.utils import generate_task_id
    from omnigent.entities import MessageData, NewConversationItem

    message_item = NewConversationItem(
        type="message",
        response_id=generate_task_id(),
        data=MessageData(
            role="user",
            content=[{"type": "input_text", "text": params.instructions}],
        ),
    )
    await asyncio.to_thread(
        conversation_store.append,
        worker_conv_id,
        [message_item],
    )

    execution = start_execution_for_item(
        task=task,
        item=item,
        task_event_store=task_event_store,
        conversation_id=worker_conv_id,
        status="running",
    )
    mark_execution_running(
        task_event_store,
        execution.id,
        conversation_id=worker_conv_id,
    )
    await asyncio.to_thread(task_item_store.update_item, item.id, state="running")
    sync_task_activity_state(
        task,
        task_store=task_store,
        task_item_store=task_item_store,
    )
    refreshed = await asyncio.to_thread(task_event_store.get_execution, execution.id)
    assert refreshed is not None
    return refreshed, worker_conv_id


def _worker_profile_from_params(
    params: DispatchParams,
    task: Task,
) -> TaskRoleProfile:
    """Build a minimal TaskRoleProfile from resolved dispatch params.

    ``build_role_session_request`` expects a profile, but the dispatcher
    has already resolved the bootstrap params. This adapter reconstructs
    just enough for the builder to work.
    """
    return TaskRoleProfile(
        role=params.role_key,
        kind="worker",
        agent_profile_id=params.agent_profile_id,
        host_id=params.host_id,
        workspace=params.workspace,
        harness=params.harness,
        model=params.model,
        created_at=0,
    )
