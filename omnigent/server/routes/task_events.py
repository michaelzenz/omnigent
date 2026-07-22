"""Routes for managed task events (``/v1/task-events``)."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from omnigent.agent_tasks.resolve import dismiss_task_event, resolve_task_event
from omnigent.db.enum_codecs import TASK_EVENT_STATE
from omnigent.entities import Task, TaskEvent, TaskEventRoutingAttempt
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.routing import RunnerRouter
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_user
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.secretary_profile_store import SecretaryProfileStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

_VALID_EVENT_STATES = frozenset(TASK_EVENT_STATE)


class ResolveTaskEventRequest(BaseModel):
    """Request body for ``POST /v1/task-events/{event_id}/resolve``."""

    resolution: Literal["route_to_task", "select_attempt"]
    task_id: str | None = None
    routing_attempt_id: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None


class BatchResolveTaskEventsRequest(BaseModel):
    """Request body for ``POST /v1/task-events/batch-resolve``."""

    event_ids: list[str] = Field(min_length=1)
    resolution: Literal["route_to_task"] = "route_to_task"
    task_id: str
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None

    @field_validator("event_ids")
    @classmethod
    def _non_empty_ids(cls, value: list[str]) -> list[str]:
        cleaned = [event_id.strip() for event_id in value if event_id.strip()]
        if not cleaned:
            raise ValueError("event_ids must contain at least one id")
        return cleaned


def _event_to_response(event: TaskEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "object": "agent.task.event",
        "event_type": event.event_type,
        "title": event.title,
        "payload": event.payload,
        "source": event.source,
        "source_key": event.source_key,
        "source_offset": event.source_offset,
        "source_session_id": event.source_session_id,
        "search_text": event.search_text,
        "summary": event.summary,
        "state": event.state,
        "priority": event.priority,
        "task_id": event.task_id,
        "manager_agent_id": event.manager_agent_id,
        "manager_conversation_id": event.manager_conversation_id,
        "selected_routing_attempt_id": event.selected_routing_attempt_id,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
        "routed_at": event.routed_at,
        "processed_at": event.processed_at,
    }


def _attempt_to_response(attempt: TaskEventRoutingAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "object": "agent.task.routing_attempt",
        "event_id": attempt.event_id,
        "candidate_task_id": attempt.candidate_task_id,
        "candidate_manager_agent_id": attempt.candidate_manager_agent_id,
        "rank": attempt.rank,
        "score": attempt.score,
        "decision": attempt.decision,
        "manager_reason": attempt.manager_reason,
        "proposed_at": attempt.proposed_at,
        "responded_at": attempt.responded_at,
        "selected_at": attempt.selected_at,
    }


def create_task_events_router(
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    secretary_profile_store: SecretaryProfileStore | None = None,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build the managed task-event router."""
    router = APIRouter()

    def _is_admin(user_id: str | None) -> bool:
        if user_id is None or permission_store is None:
            return False
        return permission_store.is_admin(user_id)

    def _require_task_access(task: Task, user_id: str | None) -> None:
        if auth_provider is None or user_id is None or _is_admin(user_id):
            return
        if task.owner_user_id is not None and task.owner_user_id != user_id:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)

    async def _get_event_or_404(event_id: str) -> TaskEvent:
        event = await asyncio.to_thread(task_event_store.get_event, event_id)
        if event is None:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        return event

    async def _load_secretary_profile(user_id: str | None) -> UserSecretaryProfile | None:
        if secretary_profile_store is None:
            return None
        effective_user_id = user_id if user_id is not None else "__anonymous__"
        return await asyncio.to_thread(secretary_profile_store.get, effective_user_id)

    def _runner_router(request: Request) -> RunnerRouter | None:
        return getattr(request.app.state, "runner_router", None)

    @router.get("/task-events")
    async def list_task_events(
        request: Request,
        state: str | None = None,
        task_id: str | None = None,
        manager_agent_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        """List task events visible to the caller."""
        user_id = get_user_id(request, auth_provider)
        if state is not None and state not in _VALID_EVENT_STATES:
            allowed = ", ".join(sorted(_VALID_EVENT_STATES))
            raise OmnigentError(
                f"state must be one of: {allowed}",
                code=ErrorCode.INVALID_INPUT,
            )
        events = await asyncio.to_thread(
            task_event_store.list_events,
            state=state,
            task_id=task_id,
            manager_agent_id=manager_agent_id,
        )
        if task_id is not None and user_id is not None and not _is_admin(user_id):
            task = await asyncio.to_thread(task_store.get, task_id)
            if task is None:
                raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
            _require_task_access(task, user_id)
        return {
            "object": "list",
            "data": [_event_to_response(event) for event in events[:limit]],
        }

    @router.get("/task-events/{event_id}")
    async def get_task_event(request: Request, event_id: str) -> dict[str, Any]:
        """Return one task event with routing attempts."""
        event = await _get_event_or_404(event_id)
        attempts = await asyncio.to_thread(task_event_store.list_routing_attempts, event_id)
        tags = await asyncio.to_thread(task_event_store.get_event_tags, event_id)
        payload = _event_to_response(event)
        payload["routing_attempts"] = [_attempt_to_response(attempt) for attempt in attempts]
        payload["tags"] = [
            {"tag_type": tag.tag_type, "tag": tag.tag}
            for tag in tags
        ]
        return payload

    @router.post("/task-events/{event_id}/resolve")
    async def resolve_event(
        request: Request,
        event_id: str,
        body: ResolveTaskEventRequest,
    ) -> dict[str, Any]:
        """Route a stalled event to a task manager."""
        user_id = require_user(request, auth_provider)
        event = await _get_event_or_404(event_id)
        task: Task | None = None
        if body.resolution == "route_to_task":
            if body.task_id is None:
                raise OmnigentError("task_id is required", code=ErrorCode.INVALID_INPUT)
            task = await asyncio.to_thread(task_store.get, body.task_id)
            if task is None:
                raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
            _require_task_access(task, user_id)
        profile = await _load_secretary_profile(user_id)
        updated = await resolve_task_event(
            event=event,
            resolution=body.resolution,
            task_store=task_store,
            task_event_store=task_event_store,
            conversation_store=conversation_store,
            runner_router=_runner_router(request),
            task=task,
            routing_attempt_id=body.routing_attempt_id,
            resolved_by_user_id=user_id,
            host_id=body.host_id,
            workspace=body.workspace,
            harness=body.harness,
            model=body.model,
            secretary_profile=profile,
        )
        return _event_to_response(updated)

    @router.post("/task-events/batch-resolve")
    async def batch_resolve_events(
        request: Request,
        body: BatchResolveTaskEventsRequest,
    ) -> dict[str, Any]:
        """Route multiple stalled events to the same task manager."""
        user_id = require_user(request, auth_provider)
        task = await asyncio.to_thread(task_store.get, body.task_id)
        if task is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
        _require_task_access(task, user_id)
        profile = await _load_secretary_profile(user_id)
        runner_router = _runner_router(request)
        resolved: list[dict[str, Any]] = []
        for event_id in body.event_ids:
            event = await _get_event_or_404(event_id)
            updated = await resolve_task_event(
                event=event,
                resolution="route_to_task",
                task_store=task_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                runner_router=runner_router,
                task=task,
                resolved_by_user_id=user_id,
                host_id=body.host_id,
                workspace=body.workspace,
                harness=body.harness,
                model=body.model,
                secretary_profile=profile,
            )
            resolved.append(_event_to_response(updated))
        return {"object": "list", "data": resolved}

    @router.post("/task-events/{event_id}/dismiss")
    async def dismiss_event(request: Request, event_id: str) -> dict[str, Any]:
        """Dismiss a task event without routing it."""
        require_user(request, auth_provider)
        event = await _get_event_or_404(event_id)
        updated = await dismiss_task_event(event=event, task_event_store=task_event_store)
        return _event_to_response(updated)

    return router
