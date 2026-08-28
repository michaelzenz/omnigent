"""Routes for managed task events (``/v1/task-events``)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from omnigent.agent_tasks.agent_builtins import TASK_BROKER_ROLE
from omnigent.agent_tasks.constants import UNRECONCILED_EVENT_STATES
from omnigent.agent_tasks.event_types import is_session_internal_event
from omnigent.agent_tasks.ingress import ingress_event
from omnigent.agent_tasks.resolve import dismiss_task_event, resolve_task_event
from omnigent.db.enum_codecs import TASK_EVENT_STATE
from omnigent.db.utils import now_epoch
from omnigent.entities import EventTag, Task, TaskEvent, TaskEventRoutingAttempt
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.host.identity import HOST_ID_HEADER
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_user
from omnigent.stores.agent_task.tags import tags_to_payload
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore

_VALID_EVENT_STATES = frozenset(TASK_EVENT_STATE)


class EventTagInput(BaseModel):
    """One typed tag on an ingress task event."""

    tag_type: str
    tag: str

    @field_validator("tag_type", "tag")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be a non-empty string")
        return stripped


class CreateIngressTaskEventRequest(BaseModel):
    """Request body for ``POST /v1/task-events`` ingress.

    Producers describe the event (source identity + tags); routing to tasks is
    owned by the server via subscriptions, session bindings, and tag scoring.
    """

    event_type: str
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    source_key: str | None = None
    source_offset: str = ""
    source_internal_session_id: str | None = None
    tags: list[EventTagInput] = Field(default_factory=list)

    @field_validator("event_type", "title")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be a non-empty string")
        return stripped

    @field_validator("source")
    @classmethod
    def _source_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("source_key")
    @classmethod
    def _source_key_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("source_internal_session_id")
    @classmethod
    def _source_internal_session_id_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


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
        "source_internal_session_id": event.source_internal_session_id,
        "parent_event_id": event.parent_event_id,
        "tags": tags_to_payload(event.tags or []),
        "state": event.state,
        "task_id": event.task_id,
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
        "score": attempt.score,
        "reason": attempt.reason,
        "proposed_at": attempt.proposed_at,
    }


def create_task_events_router(
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    task_role_profile_store: TaskRoleProfileStore | None = None,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
    session_creator: Any | None = None,
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

    async def _load_broker_profile() -> TaskRoleProfile | None:
        if task_role_profile_store is None:
            return None
        return await asyncio.to_thread(task_role_profile_store.get, TASK_BROKER_ROLE)

    def _effective_user_id(user_id: str | None) -> str:
        return user_id if user_id is not None else "__anonymous__"

    def _require_ingress_auth(request: Request) -> str | None:
        user_id = get_user_id(request, auth_provider)
        poller_host_id = request.headers.get(HOST_ID_HEADER)
        if poller_host_id is not None:
            poller_host_id = poller_host_id.strip() or None
        if user_id is None and poller_host_id is None:
            return require_user(request, auth_provider)
        return user_id

    async def _ingress_response(event: TaskEvent) -> dict[str, Any]:
        response = _event_to_response(event)
        if event.state == "broadcast":
            deliveries = await asyncio.to_thread(
                task_event_store.list_deliveries_for_event, event.id
            )
            response["deliveries"] = [
                {"event_id": delivery.id, "task_id": delivery.task_id}
                for delivery in deliveries
            ]
        return response

    @router.post("/task-events")
    async def create_task_event_ingress(
        request: Request,
        body: CreateIngressTaskEventRequest,
    ) -> dict[str, Any]:
        """Ingest an external task event and run the ingress router."""
        user_id = _require_ingress_auth(request)
        if is_session_internal_event(body.event_type):
            raise OmnigentError(
                "session-internal event types cannot be ingressed",
                code=ErrorCode.INVALID_INPUT,
            )

        if body.source is not None and body.source_key is not None:
            existing = await asyncio.to_thread(
                task_event_store.get_event_by_source,
                source=body.source,
                source_key=body.source_key,
                source_offset=body.source_offset,
                event_type=body.event_type,
            )
            if existing is not None:
                return await _ingress_response(existing)

        profile = await _load_broker_profile()
        event_id = uuid.uuid4().hex
        tags = [EventTag(tag_type=tag.tag_type, tag=tag.tag) for tag in body.tags]

        def _create() -> TaskEvent:
            return task_event_store.create_event(
                event_id,
                body.event_type,
                body.title,
                payload=json.dumps(body.payload),
                source=body.source,
                source_key=body.source_key,
                source_offset=body.source_offset,
                source_internal_session_id=body.source_internal_session_id,
                state="received",
                tags=tags,
                owner_user_id=_effective_user_id(user_id),
            )

        created = await asyncio.to_thread(_create)
        distributed = await ingress_event(
            event=created,
            task_store=task_store,
            task_event_store=task_event_store,
            worker_store=worker_store,
            conversation_store=conversation_store,
            task_role_profile_store=task_role_profile_store,
            role_profile=profile,
            owner_user_id=_effective_user_id(user_id),
            session_creator=session_creator,
            app_state=request.app.state,
            user_id=user_id,
        )
        return await _ingress_response(distributed)

    @router.get("/task-events")
    async def list_task_events(
        request: Request,
        state: str | None = None,
        task_id: str | None = None,
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
    async def get_task_event(_request: Request, event_id: str) -> dict[str, Any]:
        """Return one task event with routing attempts."""
        event = await _get_event_or_404(event_id)
        attempts = await asyncio.to_thread(task_event_store.list_routing_attempts, event_id)
        payload = _event_to_response(event)
        payload["routing_attempts"] = [_attempt_to_response(attempt) for attempt in attempts]
        return payload

    @router.post("/task-events/{event_id}/complete")
    async def complete_task_event(request: Request, event_id: str) -> dict[str, Any]:
        """Mark a routed inbound event as reconciled by the task manager."""
        require_user(request, auth_provider)
        event = await _get_event_or_404(event_id)
        if event.state not in UNRECONCILED_EVENT_STATES:
            raise OmnigentError(
                f"Cannot complete event in state {event.state!r}",
                code=ErrorCode.CONFLICT,
            )

        def _complete() -> TaskEvent:
            updated = task_event_store.update_event(
                event_id,
                state="reconciled",
                processed_at=now_epoch(),
            )
            if updated is None:
                raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
            return updated

        updated = await asyncio.to_thread(_complete)
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
        profile = await _load_broker_profile()
        resolved: list[dict[str, Any]] = []
        for event_id in body.event_ids:
            event = await _get_event_or_404(event_id)
            updated = await resolve_task_event(
                event=event,
                task_store=task_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                task=task,
                host_id=body.host_id,
                workspace=body.workspace,
                harness=body.harness,
                model=body.model,
                role_profile=profile,
                session_creator=session_creator,
                app_state=request.app.state,
                user_id=user_id,
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
