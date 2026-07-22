"""Resolve stalled task events onto a managed task manager."""

from __future__ import annotations

import uuid
from typing import Literal

from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.agent_tasks.routing import route_event_to_task
from omnigent.agent_tasks.wake import wake_task_manager_for_event
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

_ROUTE_TO_TASK_STATES = frozenset(
    {
        "received",
        "routing",
        "awaiting_new_manager_decision",
    }
)
_SELECT_ATTEMPT_STATES = frozenset({"awaiting_user_selection"})
_DISMISSABLE_STATES = frozenset(
    {
        "received",
        "routing",
        "awaiting_user_selection",
        "awaiting_new_manager_decision",
    }
)


def _generate_resolution_id() -> str:
    return uuid.uuid4().hex


async def dismiss_task_event(
    *,
    event: TaskEvent,
    task_event_store: TaskEventStore,
) -> TaskEvent:
    """Mark an event dismissed without routing it to a manager."""
    if event.state not in _DISMISSABLE_STATES:
        raise OmnigentError(
            f"Cannot dismiss event in state {event.state!r}",
            code=ErrorCode.CONFLICT,
        )
    updated = task_event_store.update_event(event.id, state="dismissed")
    if updated is None:
        raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
    return updated


async def resolve_task_event(
    *,
    event: TaskEvent,
    resolution: Literal["route_to_task", "select_attempt"],
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    runner_router: RunnerRouter | None,
    task: Task | None = None,
    routing_attempt_id: str | None = None,
    resolved_by_user_id: str | None = None,
    host_id: str | None = None,
    workspace: str | None = None,
    harness: str | None = None,
    model: str | None = None,
    secretary_profile: UserSecretaryProfile | None = None,
    wake: bool = True,
) -> TaskEvent:
    """Route a stalled event to a task manager, bootstrapping when needed."""
    if resolution == "route_to_task":
        if event.state not in _ROUTE_TO_TASK_STATES:
            raise OmnigentError(
                f"Cannot route event in state {event.state!r}",
                code=ErrorCode.CONFLICT,
            )
        if task is None:
            raise OmnigentError("task_id is required", code=ErrorCode.INVALID_INPUT)
        target_task = task
        selected_attempt_id: str | None = None
    else:
        if event.state not in _SELECT_ATTEMPT_STATES:
            raise OmnigentError(
                f"Cannot select attempt for event in state {event.state!r}",
                code=ErrorCode.CONFLICT,
            )
        if routing_attempt_id is None:
            raise OmnigentError(
                "routing_attempt_id is required",
                code=ErrorCode.INVALID_INPUT,
            )
        attempts = task_event_store.list_routing_attempts(event.id)
        attempt = next((row for row in attempts if row.id == routing_attempt_id), None)
        if attempt is None:
            raise OmnigentError("Routing attempt not found", code=ErrorCode.NOT_FOUND)
        if attempt.decision != "accepted":
            raise OmnigentError(
                "Only accepted routing attempts can be selected",
                code=ErrorCode.CONFLICT,
            )
        loaded_task = task_store.get(attempt.candidate_task_id)
        if loaded_task is None:
            raise OmnigentError("Candidate task not found", code=ErrorCode.NOT_FOUND)
        target_task = loaded_task
        selected_attempt_id = attempt.id
        for row in attempts:
            if row.id == attempt.id:
                task_event_store.update_routing_attempt(
                    row.id,
                    decision="selected",
                    selected_at=now_epoch(),
                )
            elif row.decision == "accepted":
                task_event_store.update_routing_attempt(row.id, decision="not_selected")

    params = resolve_bootstrap_params(
        host_id=host_id,
        workspace=workspace,
        harness=harness,
        model=model,
        secretary_profile=secretary_profile,
    )
    if selected_attempt_id is not None:
        task_event_store.create_resolution(
            _generate_resolution_id(),
            event.id,
            selected_attempt_id,
            target_task.id,
            target_task.manager_agent_id,
            resolved_by_user_id=resolved_by_user_id,
        )
    updated = route_event_to_task(
        event=event,
        task=target_task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        params=params,
        selected_attempt_id=selected_attempt_id,
    )
    if wake and updated.manager_conversation_id is not None:
        await wake_task_manager_for_event(
            manager_conversation_id=updated.manager_conversation_id,
            event=updated,
            conversation_store=conversation_store,
            runner_router=runner_router,
        )
    return updated
