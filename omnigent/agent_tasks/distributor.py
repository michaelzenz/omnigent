"""Distribute inbound task events to managed task managers."""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from omnigent.agent_tasks.bootstrap import BootstrapParams, resolve_bootstrap_params
from omnigent.agent_tasks.constants import AUTO_ROUTE_MIN_CONFIDENCE, distributor_agent_enabled
from omnigent.agent_tasks.distributor_queue import enqueue_distributor_event
from omnigent.agent_tasks.event_types import is_distributor_candidate
from omnigent.agent_tasks.routing import ROUTED_EVENT_STATE, route_event_to_task
from omnigent.agent_tasks.scoring import (
    candidates_above_threshold,
    pick_auto_route,
    rank_tasks_for_event,
)
from omnigent.agent_tasks.wake import (
    wake_secretary_for_stalled_events,
    wake_task_manager_for_event,
)
from omnigent.entities import Task, TaskEvent
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.secretary_profile_store import SecretaryProfileStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

_logger = logging.getLogger(__name__)

StallReason = Literal["user_selection", "new_manager_decision"]


def _bootstrap_params(secretary_profile: UserSecretaryProfile | None) -> BootstrapParams:
    return resolve_bootstrap_params(
        host_id=secretary_profile.host_id if secretary_profile else None,
        workspace=secretary_profile.workspace if secretary_profile else None,
        harness=secretary_profile.harness if secretary_profile else None,
        model=secretary_profile.model if secretary_profile else None,
        secretary_profile=secretary_profile,
    )


def _generate_attempt_id() -> str:
    return uuid.uuid4().hex


def _record_routing_attempts(
    *,
    event_id: str,
    ranked: list[tuple[Task, float]],
    task_event_store: TaskEventStore,
) -> None:
    for rank, (task, score) in enumerate(ranked, start=1):
        decision = "accepted" if score >= AUTO_ROUTE_MIN_CONFIDENCE else "proposed"
        task_event_store.create_routing_attempt(
            _generate_attempt_id(),
            event_id,
            task.id,
            task.manager_agent_id,
            rank,
            score=score,
            decision=decision,
        )


async def distribute_event(
    *,
    event: TaskEvent,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    runner_router: RunnerRouter | None,
    secretary_profile_store: SecretaryProfileStore | None = None,
    secretary_profile: UserSecretaryProfile | None = None,
    owner_user_id: str | None = None,
) -> TaskEvent:
    """
    Route a distributor-candidate event to a task manager or stall for secretary help.

    Idempotent when the event is already routed or awaiting manager triage.
    """
    if not is_distributor_candidate(event_type=event.event_type, task_id=event.task_id):
        return event
    if event.state in {ROUTED_EVENT_STATE, "reconciled"}:
        return event

    routing = task_event_store.update_event(event.id, state="routing")
    if routing is None:
        raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
    event = routing

    if event.task_id is not None:
        bound_task = task_store.get(event.task_id)
        if bound_task is not None and bound_task.state == "active":
            params = _bootstrap_params(secretary_profile)
            return await _finish_route(
                event=event,
                task=bound_task,
                task_store=task_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                agent_store=agent_store,
                runner_router=runner_router,
                params=params,
                secretary_profile_store=secretary_profile_store,
                owner_user_id=owner_user_id,
            )
        return await _stall(
            event=event,
            reason="new_manager_decision",
            ranked=[],
            task_event_store=task_event_store,
            secretary_profile_store=secretary_profile_store,
            conversation_store=conversation_store,
            runner_router=runner_router,
            owner_user_id=owner_user_id,
        )

    if event.source_session_id:
        binding = task_event_store.get_binding(event.source_session_id)
        if binding is not None:
            bound_task = task_store.get(binding.task_id)
            if bound_task is not None:
                params = _bootstrap_params(secretary_profile)
                return await _finish_route(
                    event=event,
                    task=bound_task,
                    task_store=task_store,
                    task_event_store=task_event_store,
                    conversation_store=conversation_store,
                    agent_store=agent_store,
                    runner_router=runner_router,
                    params=params,
                    secretary_profile_store=secretary_profile_store,
                    owner_user_id=owner_user_id,
                )

    active_tasks = task_store.list(state="active")
    if not active_tasks:
        return await _stall(
            event=event,
            reason="new_manager_decision",
            ranked=[],
            task_event_store=task_event_store,
            secretary_profile_store=secretary_profile_store,
            conversation_store=conversation_store,
            runner_router=runner_router,
            owner_user_id=owner_user_id,
        )

    prefiltered: list[Task] = []
    seen_ids: set[str] = set()
    for task in task_store.search(event.search_text, limit=20):
        if task.id not in seen_ids and task.state == "active":
            prefiltered.append(task)
            seen_ids.add(task.id)
    for task in active_tasks:
        if task.id not in seen_ids:
            prefiltered.append(task)
            seen_ids.add(task.id)

    ranked = rank_tasks_for_event(event_search_text=event.search_text, tasks=prefiltered)
    _record_routing_attempts(event_id=event.id, ranked=ranked, task_event_store=task_event_store)

    if distributor_agent_enabled():
        return await _stall(
            event=event,
            reason="new_manager_decision",
            ranked=ranked,
            task_event_store=task_event_store,
            secretary_profile_store=secretary_profile_store,
            conversation_store=conversation_store,
            runner_router=runner_router,
            owner_user_id=owner_user_id,
        )

    auto_task = pick_auto_route(ranked)
    if auto_task is not None:
        params = _bootstrap_params(secretary_profile)
        return await _finish_route(
            event=event,
            task=auto_task,
            task_store=task_store,
            task_event_store=task_event_store,
            conversation_store=conversation_store,
            agent_store=agent_store,
            runner_router=runner_router,
            params=params,
            secretary_profile_store=secretary_profile_store,
            owner_user_id=owner_user_id,
        )

    above = candidates_above_threshold(ranked)
    if above:
        return await _stall(
            event=event,
            reason="user_selection",
            ranked=above,
            task_event_store=task_event_store,
            secretary_profile_store=secretary_profile_store,
            conversation_store=conversation_store,
            runner_router=runner_router,
            owner_user_id=owner_user_id,
        )

    return await _stall(
        event=event,
        reason="new_manager_decision",
        ranked=ranked,
        task_event_store=task_event_store,
        secretary_profile_store=secretary_profile_store,
        conversation_store=conversation_store,
        runner_router=runner_router,
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
    runner_router: RunnerRouter | None,
    params: BootstrapParams,
    secretary_profile_store: SecretaryProfileStore | None = None,
    owner_user_id: str | None = None,
    selected_attempt_id: str | None = None,
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
            selected_attempt_id=selected_attempt_id,
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
            reason="new_manager_decision",
            ranked=[(task, 1.0)],
            task_event_store=task_event_store,
            secretary_profile_store=secretary_profile_store,
            conversation_store=conversation_store,
            runner_router=runner_router,
            owner_user_id=owner_user_id,
        )

    if updated.manager_conversation_id is not None:
        await wake_task_manager_for_event(
            manager_conversation_id=updated.manager_conversation_id,
            event=updated,
            conversation_store=conversation_store,
            runner_router=runner_router,
        )
    return updated


async def _stall(
    *,
    event: TaskEvent,
    reason: StallReason,
    ranked: list[tuple[Task, float]],
    task_event_store: TaskEventStore,
    secretary_profile_store: SecretaryProfileStore | None,
    conversation_store: ConversationStore,
    runner_router: RunnerRouter | None,
    owner_user_id: str | None,
) -> TaskEvent:
    stall_state = "awaiting_grouping"
    if not distributor_agent_enabled() and reason == "user_selection":
        stall_state = "awaiting_user_selection"
    updated = task_event_store.update_event(event.id, state=stall_state)
    if updated is None:
        raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
    if distributor_agent_enabled():
        await enqueue_distributor_event(
            event_id=updated.id,
            owner_user_id=owner_user_id if owner_user_id is not None else "__anonymous__",
            ranked=ranked,
        )
        return updated
    if secretary_profile_store is not None:
        await wake_secretary_for_stalled_events(
            user_id=owner_user_id if owner_user_id is not None else "__anonymous__",
            events=[updated],
            ranked_candidates={updated.id: ranked},
            secretary_profile_store=secretary_profile_store,
            conversation_store=conversation_store,
            runner_router=runner_router,
        )
    return updated
