"""Events on the caller's GlobalHub board (``/v1/task-events``)."""

from __future__ import annotations

import asyncio
import uuid
from typing import cast

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import SESSION_EVENT_SOURCE, EventTag, TaskEvent
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes.agent_task_workers import adopt_worker
from omnigent.server.schemas import (
    AgentTaskEventAssignment,
    AgentTaskEventList,
    AgentTaskEventResponse,
    AgentTaskEventState,
    AgentTaskEventTag,
    AssignAgentTaskEventRequest,
    CreateAgentTaskEventRequest,
    RouteAgentTaskEventsRequest,
)
from omnigent.stores import ConversationStore
from omnigent.stores.task_subscription_store import SourceKeyError

# Session events come only from the server's own session hooks.
_SESSION_EVENT_PREFIX = "session."


def create_agent_task_events_router(
    board: BoardStores,
    auth_provider: AuthProvider | None = None,
    conversation_store: ConversationStore | None = None,
) -> APIRouter:
    """Build the router for the events on the caller's GlobalHub board.

    :param board: The board's events, tasks, managers, workers and subscriptions.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :param conversation_store: Confirms that a session event's session still
        exists before it becomes a worker; ``None`` skips the check.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    events = board.events
    managers = board.managers
    subscriptions = board.subscriptions

    @router.post("/task-events", response_model=AgentTaskEventResponse)
    async def create_event(
        request: Request, body: CreateAgentTaskEventRequest
    ) -> AgentTaskEventResponse:
        """Record an event on the caller's board.

        Each live task that follows the event's source key gets a copy through
        its manager, and the response is the first copy; without such a task,
        the broker routes the event. A report that names its source and source
        key is recorded once.
        """
        user_id = require_user(request, auth_provider)
        if (
            body.event_type.startswith(_SESSION_EVENT_PREFIX)
            or body.source == SESSION_EVENT_SOURCE
        ):
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
            followers = (
                subscriptions.followers(
                    owner_user_id=user_id, source=body.source, source_key=body.source_key
                )
                if body.source_key is not None
                else []
            )
            targets = [(follower.task_id, follower.manager_id) for follower in followers]
            created = [
                events.create(
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
                    task_id=task_id,
                    manager_id=manager_id,
                )
                for task_id, manager_id in targets or [(None, None)]
            ]
            return created[0]

        return task_event_response(await asyncio.to_thread(create))

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
        return AgentTaskEventList(data=[task_event_response(event) for event in routed])

    @router.post("/task-events/{event_id}/dismiss", response_model=AgentTaskEventResponse)
    async def dismiss_event(request: Request, event_id: str) -> AgentTaskEventResponse:
        """Mark one of the caller's events handled, or noise.

        Dismissing an event again changes nothing.
        """
        user_id = require_user(request, auth_provider)
        event = await asyncio.to_thread(events.dismiss, event_id, owner_user_id=user_id)
        if event is None:
            raise OmnigentError("task event not found", code=ErrorCode.NOT_FOUND)
        return task_event_response(event)

    @router.post("/task-events/{event_id}/assign", response_model=AgentTaskEventAssignment)
    async def assign_event(
        request: Request, event_id: str, body: AssignAgentTaskEventRequest
    ) -> AgentTaskEventAssignment:
        """Assign one of the caller's events to one of their tasks, and mark it handled.

        The task then follows the event's key, so later events with the key go
        to the task's manager. A session event makes the session the task's
        worker (``title`` names the worker); another event with a source key
        subscribes the task to it. Assigning again changes only the worker's
        title. An event filed into an FYI cluster cannot be assigned.
        """
        user_id = require_user(request, auth_provider)

        def assign() -> AgentTaskEventAssignment:
            event = events.get(event_id, owner_user_id=user_id)
            if event is None:
                raise OmnigentError("task event not found", code=ErrorCode.NOT_FOUND)
            if event.state == "fyi":
                raise OmnigentError(
                    "the event is filed in an FYI cluster", code=ErrorCode.CONFLICT
                )
            task = board.tasks.get(body.task_id, owner_user_id=user_id)
            if task is None:
                raise OmnigentError("task not found", code=ErrorCode.NOT_FOUND)
            worker_id = subscription_id = None
            if event.source == SESSION_EVENT_SOURCE and event.source_key:
                session_id = event.source_key
                # A deleted session does not become a worker.
                if (
                    conversation_store is None
                    or conversation_store.get_conversation(session_id) is not None
                ):
                    worker, _ = adopt_worker(
                        board.workers,
                        owner_user_id=user_id,
                        task_id=task.id,
                        session_id=session_id,
                        title=body.title,
                    )
                    worker_id = worker.id
            elif event.source_key:
                try:
                    subscription_id = subscriptions.subscribe(
                        uuid.uuid4().hex,
                        owner_user_id=user_id,
                        task_id=task.id,
                        source_key=event.source_key,
                        source=event.source,
                    ).id
                except SourceKeyError as exc:
                    raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
            assigned = events.assign(event.id, owner_user_id=user_id, task_id=task.id)
            return AgentTaskEventAssignment(
                event=task_event_response(assigned or event),
                worker_id=worker_id,
                subscription_id=subscription_id,
            )

        return await asyncio.to_thread(assign)

    return router


def task_event_response(event: TaskEvent) -> AgentTaskEventResponse:
    """Return the API form of a task event."""
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
        fyi_cluster_id=event.fyi_cluster_id,
        created_at=event.created_at,
        updated_at=event.updated_at,
        routed_at=event.routed_at,
        processed_at=event.processed_at,
    )
