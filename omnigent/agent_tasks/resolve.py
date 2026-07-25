"""Resolve stalled task events onto a managed task manager."""

from __future__ import annotations

from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.agent_tasks.routing import route_event_to_task
from omnigent.agent_tasks.wake import wake_task_manager_for_event
from omnigent.entities import Task, TaskEvent
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

_ROUTE_TO_TASK_STATES = frozenset(
    {
        "received",
        "routing",
        "awaiting_grouping",
    }
)
_DISMISSABLE_STATES = frozenset(
    {
        "received",
        "routing",
        "awaiting_grouping",
        "routing_proposed",
        "classified_fyi",
    }
)


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
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    runner_router: RunnerRouter | None,
    task: Task,
    resolved_by_user_id: str | None = None,
    host_id: str | None = None,
    workspace: str | None = None,
    harness: str | None = None,
    model: str | None = None,
    secretary_profile: UserSecretaryProfile | None = None,
    wake: bool = True,
) -> TaskEvent:
    """Route a stalled event to a task manager, bootstrapping when needed."""
    if event.state not in _ROUTE_TO_TASK_STATES:
        raise OmnigentError(
            f"Cannot route event in state {event.state!r}",
            code=ErrorCode.CONFLICT,
        )

    params = resolve_bootstrap_params(
        host_id=host_id,
        workspace=workspace,
        harness=harness,
        model=model,
        secretary_profile=secretary_profile,
    )
    updated = route_event_to_task(
        event=event,
        task=task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
        params=params,
    )
    if wake and updated.manager_conversation_id is not None:
        await wake_task_manager_for_event(
            manager_conversation_id=updated.manager_conversation_id,
            event=updated,
            conversation_store=conversation_store,
            runner_router=runner_router,
        )
    return updated
