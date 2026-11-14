"""Events on the caller's GlobalHub board (``/v1/task-events``)."""

from __future__ import annotations

import asyncio
import uuid
from typing import cast

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import EventTag, TaskEvent
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    AgentTaskEventList,
    AgentTaskEventResponse,
    AgentTaskEventState,
    AgentTaskEventTag,
    CreateAgentTaskEventRequest,
    RouteAgentTaskEventsRequest,
)

# Session events come only from the server's own session hooks.
_SESSION_EVENT_PREFIX = "session."


def create_agent_task_events_router(
    board: BoardStores,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the events on the caller's GlobalHub board.

    :param board: The board's events and managers.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    events = board.events
    managers = board.managers

    @router.post("/task-events", response_model=AgentTaskEventResponse)
    async def create_event(
        request: Request, body: CreateAgentTaskEventRequest
    ) -> AgentTaskEventResponse:
        """Record an event for the broker to route.

        A report that names its source and source key is recorded once.
        """
        user_id = require_user(request, auth_provider)
        if body.event_type.startswith(_SESSION_EVENT_PREFIX):
            raise OmnigentError(
                "session events come only from the server's session hooks",
                code=ErrorCode.INVALID_INPUT,
            )

        def create() -> TaskEvent:
            if body.source is not None and body.source_key is not None:
                earlier = events.get_by_source(
                    owner_user_id=user_id,
                    event_type=body.event_type,
                    source=body.source,
                    source_key=body.source_key,
                    source_offset=body.source_offset,
                )
                if earlier is not None:
                    return earlier
            return events.create(
                uuid.uuid4().hex,
                owner_user_id=user_id,
                event_type=body.event_type,
                title=body.title,
                payload=body.payload,
                source=body.source,
                source_key=body.source_key,
                source_offset=body.source_offset,
                source_session_id=body.source_session_id,
                tags=[EventTag(tag_type=tag.tag_type, tag=tag.tag) for tag in body.tags],
            )

        return _event_response(await asyncio.to_thread(create))

    @router.post("/task-events/batch-route-manager", response_model=AgentTaskEventList)
    async def route_events_to_manager(
        request: Request, body: RouteAgentTaskEventsRequest
    ) -> AgentTaskEventList:
        """Route events that wait for the broker to one of the caller's managers.

        Either every event is routed or none is. Events already routed to that
        manager are left as they are, so a retry is safe.
        """
        user_id = require_user(request, auth_provider)

        def route() -> list[TaskEvent]:
            if managers.get(body.manager_id, owner_user_id=user_id) is None:
                raise OmnigentError("manager not found", code=ErrorCode.NOT_FOUND)
            for event_id in body.event_ids:
                event = events.get(event_id, owner_user_id=user_id)
                if event is None:
                    raise OmnigentError(
                        f"task event {event_id} not found", code=ErrorCode.NOT_FOUND
                    )
                routed_here = event.state == "routed" and event.manager_id == body.manager_id
                if event.state != "unrouted" and not routed_here:
                    raise OmnigentError(
                        f"task event {event_id} is {event.state}, not waiting for the broker",
                        code=ErrorCode.CONFLICT,
                    )
            routed = events.route_to_manager(
                body.event_ids, owner_user_id=user_id, manager_id=body.manager_id
            )
            if routed is None:
                raise OmnigentError("task events changed while routing", code=ErrorCode.CONFLICT)
            return routed

        routed = await asyncio.to_thread(route)
        return AgentTaskEventList(data=[_event_response(event) for event in routed])

    @router.post("/task-events/{event_id}/dismiss", response_model=AgentTaskEventResponse)
    async def dismiss_event(request: Request, event_id: str) -> AgentTaskEventResponse:
        """Mark one of the caller's events handled, or noise.

        Dismissing an event again changes nothing.
        """
        user_id = require_user(request, auth_provider)
        event = await asyncio.to_thread(events.dismiss, event_id, owner_user_id=user_id)
        if event is None:
            raise OmnigentError("task event not found", code=ErrorCode.NOT_FOUND)
        return _event_response(event)

    return router


def _event_response(event: TaskEvent) -> AgentTaskEventResponse:
    return AgentTaskEventResponse(
        id=event.id,
        event_type=event.event_type,
        title=event.title,
        state=cast(AgentTaskEventState, event.state),
        payload=event.payload,
        source=event.source,
        source_key=event.source_key,
        source_offset=event.source_offset,
        source_session_id=event.source_session_id,
        tags=[AgentTaskEventTag(tag_type=tag.tag_type, tag=tag.tag) for tag in event.tags],
        task_id=event.task_id,
        manager_id=event.manager_id,
        created_at=event.created_at,
        updated_at=event.updated_at,
        routed_at=event.routed_at,
        processed_at=event.processed_at,
    )
