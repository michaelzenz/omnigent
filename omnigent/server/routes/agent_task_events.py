"""Events on the caller's GlobalHub board (``/v1/task-events``)."""

from __future__ import annotations

import asyncio
import uuid
from typing import cast

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import LIVE_TASK_STATES, SESSION_EVENT_SOURCE, EventTag, TaskEvent
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
    RouteAgentTaskEventsToTaskRequest,
)
from omnigent.stores import ConversationStore
from omnigent.stores.subscription_store import (
    SourceError,
    is_reserved_source_type,
    normalize_source_key,
    normalize_source_type,
)
from omnigent.util.uuid import normalize_uuid

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
        exists before it becomes a worker, and that a manager's session can
        take events; ``None`` skips the checks.
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

        Each session that follows the event's source key gets a copy, except
        the session the event came from, and the response is the first copy.
        When only that session follows the key, the event is recorded as
        handled. When no session follows the key, the broker routes the event.
        A report that names its source type and source key is recorded once.
        """
        user_id = require_user(request, auth_provider)
        if body.event_type.startswith(_SESSION_EVENT_PREFIX):
            raise OmnigentError(
                "session events come only from the server's session hooks",
                code=ErrorCode.INVALID_INPUT,
            )
        try:
            source = normalize_source_type(body.source) if body.source is not None else None
            source_key = (
                normalize_source_key(body.source_key) if body.source_key is not None else None
            )
        except SourceError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        if source is not None and is_reserved_source_type(source):
            raise OmnigentError(
                f"the source type {source!r} is the server's own", code=ErrorCode.INVALID_INPUT
            )

        def create() -> TaskEvent:
            if source is not None and source_key is not None:
                earlier = events.get_by_source(
                    owner_user_id=user_id,
                    event_type=body.event_type,
                    source=source,
                    source_key=source_key,
                    source_offset=body.source_offset,
                )
                if earlier is not None:
                    return earlier
            followers = (
                subscriptions.subscribers(owner_user_id=user_id, source_key=source_key)
                if source_key is not None
                else []
            )
            reporter = (
                normalize_uuid(body.source_session_id) or body.source_session_id
                if body.source_session_id is not None
                else None
            )
            targets: list[str | None] = [
                follower for follower in followers if follower != reporter
            ]
            own_report = bool(followers) and not targets
            if own_report:
                targets = [reporter]
            created = [
                events.create(
                    uuid.uuid4().hex,
                    owner_user_id=user_id,
                    event_type=body.event_type,
                    title=body.title,
                    payload=body.payload,
                    source=source,
                    source_key=source_key,
                    source_offset=body.source_offset,
                    source_session_id=body.source_session_id,
                    tags=[EventTag(tag_type=tag.tag_type, tag=tag.tag) for tag in body.tags],
                    target_session_id=target,
                )
                for target in targets or [None]
            ]
            if own_report:
                return events.dismiss(created[0].id, owner_user_id=user_id) or created[0]
            return created[0]

        return task_event_response(await asyncio.to_thread(create))

    def route_to_manager(
        event_ids: list[str],
        *,
        owner_user_id: str | None,
        manager_id: str,
        task_id: str | None = None,
    ) -> list[TaskEvent]:
        manager = managers.get(manager_id, owner_user_id=owner_user_id)
        if manager is None:
            raise OmnigentError("manager not found", code=ErrorCode.NOT_FOUND)
        session_id = manager.conversation_id
        if session_id is None:
            raise OmnigentError("the manager has no session yet", code=ErrorCode.CONFLICT)
        if conversation_store is not None:
            session = conversation_store.get_conversation(session_id)
            if session is None or session.archived:
                raise OmnigentError(
                    "the manager's session is gone; route to another manager",
                    code=ErrorCode.CONFLICT,
                )
        for event_id in event_ids:
            event = events.get(event_id, owner_user_id=owner_user_id)
            if event is None:
                raise OmnigentError(f"task event {event_id} not found", code=ErrorCode.NOT_FOUND)
            routed_here = (
                event.state == "routed"
                and event.target_session_id == session_id
                and event.task_id == task_id
            )
            if event.state != "unrouted" and not routed_here:
                raise OmnigentError(
                    f"task event {event_id} is {event.state}, not waiting for the broker",
                    code=ErrorCode.CONFLICT,
                )
        routed = events.route_to_session(
            event_ids, owner_user_id=owner_user_id, session_id=session_id, task_id=task_id
        )
        if routed is None:
            raise OmnigentError("task events changed while routing", code=ErrorCode.CONFLICT)
        return routed

    @router.post("/task-events/batch-route-manager", response_model=AgentTaskEventList)
    async def route_events_to_manager(
        request: Request, body: RouteAgentTaskEventsRequest
    ) -> AgentTaskEventList:
        """Route events that wait for the broker to the session of one of the caller's managers.

        Either every event is routed or none is. Events already routed to that
        manager are left as they are, so a retry is safe. A manager whose
        session is gone or archived takes no events. The manager does not
        follow the events' sources: a later event about a source that no
        session follows comes to the broker again.
        """
        user_id = require_user(request, auth_provider)
        routed = await asyncio.to_thread(
            route_to_manager, body.event_ids, owner_user_id=user_id, manager_id=body.manager_id
        )
        return AgentTaskEventList(data=[task_event_response(event) for event in routed])

    @router.post("/task-events/batch-route-task", response_model=AgentTaskEventList)
    async def route_events_to_task(
        request: Request, body: RouteAgentTaskEventsToTaskRequest
    ) -> AgentTaskEventList:
        """Route events that wait for the broker to the manager of one of the caller's tasks.

        Each event records the task, and the manager's notice names it. Either
        every event is routed or none is. Events already routed to that task
        are left as they are, so a retry is safe. An archived task, or a task
        without a manager, takes no events. The manager does not follow the
        events' sources.
        """
        user_id = require_user(request, auth_provider)

        def route() -> list[TaskEvent]:
            task = board.tasks.get(body.task_id, owner_user_id=user_id)
            if task is None:
                raise OmnigentError("task not found", code=ErrorCode.NOT_FOUND)
            if task.state not in LIVE_TASK_STATES:
                raise OmnigentError(
                    "the task is archived; route to a manager", code=ErrorCode.CONFLICT
                )
            if task.manager_id is None:
                raise OmnigentError(
                    "the task has no manager; route to a manager", code=ErrorCode.CONFLICT
                )
            return route_to_manager(
                body.event_ids,
                owner_user_id=user_id,
                manager_id=task.manager_id,
                task_id=task.id,
            )

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

        The assignment is a record on the event. A session event also makes the
        session the task's worker (``title`` names the worker), which the task's
        manager then follows. Assigning again changes only the worker's title.
        An event filed into an FYI cluster cannot be assigned.
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
            worker_id = None
            if event.source == SESSION_EVENT_SOURCE and event.source_key:
                session_id = event.source_key
                # A deleted session does not become a worker.
                if (
                    conversation_store is None
                    or conversation_store.get_conversation(session_id) is not None
                ):
                    worker, _ = adopt_worker(
                        board,
                        owner_user_id=user_id,
                        task=task,
                        session_id=session_id,
                        title=body.title,
                    )
                    worker_id = worker.id
            assigned = events.assign(event.id, owner_user_id=user_id, task_id=task.id)
            return AgentTaskEventAssignment(
                event=task_event_response(assigned or event), worker_id=worker_id
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
        target_session_id=event.target_session_id,
        fyi_cluster_id=event.fyi_cluster_id,
        created_at=event.created_at,
        updated_at=event.updated_at,
        routed_at=event.routed_at,
        processed_at=event.processed_at,
    )
