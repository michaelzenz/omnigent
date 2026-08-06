"""Ingress — route inbound task events to managed task managers or stall for the broker."""

from __future__ import annotations

import logging

from omnigent.agent_tasks.bootstrap import BootstrapParams, resolve_bootstrap_params
from omnigent.agent_tasks.event_types import is_ingress_candidate
from omnigent.agent_tasks.routing import ROUTED_EVENT_STATE, route_event_to_task
from omnigent.agent_tasks.scoring import (
    candidate_task_ids_for_event_tags,
    pick_auto_route,
    rank_tasks_for_event_tags,
)
from omnigent.agent_tasks.session_task import task_for_session
from omnigent.agent_tasks.task_match import _LIVE_TASK_STATES, live_tasks
from omnigent.entities import Task, TaskEvent
from omnigent.entities.task_role_profile import UserTaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore

_logger = logging.getLogger(__name__)


def _bootstrap_params(role_profile: UserTaskRoleProfile | None) -> BootstrapParams:
    return resolve_bootstrap_params(
        host_id=role_profile.host_id if role_profile else None,
        workspace=role_profile.workspace if role_profile else None,
        harness=role_profile.harness if role_profile else None,
        model=role_profile.model if role_profile else None,
        role_profile=role_profile,
    )


async def ingress_event(
    *,
    event: TaskEvent,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    task_role_profile_store: TaskRoleProfileStore | None = None,
    role_profile: UserTaskRoleProfile | None = None,
    owner_user_id: str | None = None,
) -> TaskEvent:
    """
    Route an ingress-candidate event to a task manager or stall for broker help.

    Idempotent when the event is already routed or awaiting manager triage.
    """
    if not is_ingress_candidate(event_type=event.event_type, task_id=event.task_id):
        return event
    if event.state in {ROUTED_EVENT_STATE, "reconciled"}:
        return event

    if event.task_id is not None:
        bound_task = task_store.get(event.task_id)
        if bound_task is not None and bound_task.state in _LIVE_TASK_STATES:
            params = _bootstrap_params(role_profile)
            return await _finish_route(
                event=event,
                task=bound_task,
                task_store=task_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                agent_store=agent_store,
                params=params,
                task_role_profile_store=task_role_profile_store,
                owner_user_id=owner_user_id,
                routing_reason="explicit-task",
            )
        return await _stall(
            event=event,
            task_event_store=task_event_store,
            owner_user_id=owner_user_id,
        )

    if event.source_internal_session_id:
        bound_task = task_for_session(
            event.source_internal_session_id,
            task_store=task_store,
            worker_store=worker_store,
        )
        if bound_task is not None:
            params = _bootstrap_params(role_profile)
            return await _finish_route(
                event=event,
                task=bound_task,
                task_store=task_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                agent_store=agent_store,
                params=params,
                task_role_profile_store=task_role_profile_store,
                owner_user_id=owner_user_id,
                routing_reason="session-binding",
            )

    active_tasks = live_tasks(task_store)
    if not active_tasks:
        return await _stall(
            event=event,
            task_event_store=task_event_store,
            owner_user_id=owner_user_id,
        )

    event_tags = event.tags or []
    if not event_tags:
        return await _stall(
            event=event,
            task_event_store=task_event_store,
            owner_user_id=owner_user_id,
        )

    candidate_ids = candidate_task_ids_for_event_tags(event_tags, task_store=task_store)
    prefiltered = [task for task in active_tasks if task.id in candidate_ids]
    if not prefiltered:
        return await _stall(
            event=event,
            task_event_store=task_event_store,
            owner_user_id=owner_user_id,
        )

    ranked = rank_tasks_for_event_tags(
        event_tags=event_tags,
        tasks=prefiltered,
        task_store=task_store,
    )
    auto_task = pick_auto_route(ranked)
    if auto_task is not None:
        auto_score = 0.0
        for task, score in ranked:
            if task.id == auto_task.id:
                auto_score = score
                break
        params = _bootstrap_params(role_profile)
        return await _finish_route(
            event=event,
            task=auto_task,
            task_store=task_store,
            task_event_store=task_event_store,
            conversation_store=conversation_store,
            agent_store=agent_store,
            params=params,
            task_role_profile_store=task_role_profile_store,
            owner_user_id=owner_user_id,
            routing_reason=f"auto-route score={auto_score:.4f}",
            routing_score=auto_score,
        )

    return await _stall(
        event=event,
        task_event_store=task_event_store,
        owner_user_id=owner_user_id,
    )


async def _finish_route(
    *,
    event: TaskEvent,
    task: Task,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    params: BootstrapParams,
    task_role_profile_store: TaskRoleProfileStore | None = None,
    owner_user_id: str | None = None,
    routing_reason: str | None = None,
    routing_score: float | None = None,
) -> TaskEvent:
    try:
        updated = route_event_to_task(
            event=event,
            task=task,
            task_store=task_store,
            task_event_store=task_event_store,
            conversation_store=conversation_store,
            agent_store=agent_store,
            params=params,
            routing_reason=routing_reason,
            routing_score=routing_score,
        )
    except OmnigentError as exc:
        if exc.code != ErrorCode.INVALID_INPUT:
            raise
        _logger.warning(
            "auto-route bootstrap failed for event %s task %s: %s",
            event.id,
            task.id,
            exc,
        )
        return await _stall(
            event=event,
            task_event_store=task_event_store,
            owner_user_id=owner_user_id,
        )

    routed_task = task_store.get(task.id)
    _ = routed_task  # the event is now routed; the manager packager picks it up.
    return updated


async def _stall(
    *,
    event: TaskEvent,
    task_event_store: TaskEventStore,
    owner_user_id: str | None,
) -> TaskEvent:
    effective_owner = owner_user_id if owner_user_id is not None else "__anonymous__"
    updated = task_event_store.update_event(
        event.id,
        state="awaiting_grouping",
        owner_user_id=effective_owner,
    )
    if updated is None:
        raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
    return updated
