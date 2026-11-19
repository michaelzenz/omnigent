"""The sources that sessions follow, and the watcher of each source type.

A session follows a source, e.g. a pull request, with the ``event_subscriptions``
tool or these routes; each event about the source then goes to the session. The
custodian assigns each source type to the watcher whose manual covers it, and
the fallback watcher checks the types without one. Include this router before
the tasks router: ``/agent-tasks/{task_id}`` would otherwise match
``/agent-tasks/source-types``.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.role_sessions import RoleSessionStores, current_role_session
from omnigent.agent_tasks.roles import CUSTODIAN_ROLE_KEY, builtin_role
from omnigent.agent_tasks.source_assignment import request_source_assignment
from omnigent.entities import Subscription
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ, AuthProvider
from omnigent.server.routes._agent_task_callers import runner_bound_caller
from omnigent.server.routes._auth_helpers import (
    get_user_id,
    require_access_and_level,
    require_user,
)
from omnigent.server.routes._errors import session_not_found
from omnigent.server.schemas import (
    AssignSourceTypeRequest,
    CreateSessionSubscriptionRequest,
    RemoveSessionSubscriptionRequest,
    SessionSubscriptionList,
    SessionSubscriptionRemoved,
    SessionSubscriptionResponse,
    SourceTypeList,
    SourceTypeResponse,
)
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.subscription_store import (
    SourceError,
    is_reserved_source_type,
    normalize_source_type,
)


def create_agent_task_subscriptions_router(
    board: BoardStores,
    conversation_store: ConversationStore,
    sessions: RoleSessionStores | None = None,
    permission_store: PermissionStore | None = None,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the sources that sessions follow, and their watchers.

    :param board: The board's subscriptions and watchers.
    :param conversation_store: Holds the sessions and their owners.
    :param sessions: Where the custodian session lives, which hears of new
        source types; ``None`` on servers without GlobalHub roles.
    :param permission_store: Checks the caller's access to a session; ``None``
        on servers without permissions.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    subscriptions = board.subscriptions
    custodian_role = builtin_role(CUSTODIAN_ROLE_KEY)
    assert custodian_role is not None

    async def session_owner(request: Request, session_id: str, level: int) -> str | None:
        await require_access_and_level(
            get_user_id(request, auth_provider),
            session_id,
            level,
            permission_store,
            conversation_store,
        )
        return await asyncio.to_thread(
            conversation_store.get_session_owner, session_id, owner_only=True
        )

    @router.get("/sessions/{session_id}/subscriptions", response_model=SessionSubscriptionList)
    async def list_session_subscriptions(
        request: Request, session_id: str
    ) -> SessionSubscriptionList:
        """List the sources that a session follows, in the order it started to follow them."""
        owner = await session_owner(request, session_id, LEVEL_READ)
        listed = await asyncio.to_thread(
            subscriptions.list_for_session, session_id, owner_user_id=owner
        )
        return SessionSubscriptionList(data=[subscription_response(item) for item in listed])

    @router.post(
        "/sessions/{session_id}/subscriptions", response_model=SessionSubscriptionResponse
    )
    async def subscribe_session(
        request: Request, session_id: str, body: CreateSessionSubscriptionRequest
    ) -> SessionSubscriptionResponse:
        """Make a session follow a source; each event about the source then goes to it.

        The server's own ``omnigent.`` types are refused. A type that no session
        followed before wakes the custodian, which assigns it to a watcher.
        """
        owner = await session_owner(request, session_id, LEVEL_EDIT)
        source_type = _source_type(body.source_type)

        def subscribe() -> tuple[Subscription, bool]:
            conv = conversation_store.get_conversation(session_id)
            if conv is None or conv.archived:
                raise session_not_found()
            try:
                result = subscriptions.subscribe(
                    owner_user_id=owner,
                    session_id=conv.id,
                    source_type=source_type,
                    source_key=body.source_key,
                    note=body.note,
                    url=body.url,
                )
            except SourceError as exc:
                raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
            return result.subscription, result.new_type

        subscription, new_type = await asyncio.to_thread(subscribe)
        if new_type:
            await request_source_assignment(board, sessions, user_id=owner)
        return subscription_response(subscription)

    @router.post(
        "/sessions/{session_id}/subscriptions/remove", response_model=SessionSubscriptionRemoved
    )
    async def unsubscribe_session(
        request: Request, session_id: str, body: RemoveSessionSubscriptionRequest
    ) -> SessionSubscriptionRemoved:
        """Stop a session from following a source."""
        owner = await session_owner(request, session_id, LEVEL_EDIT)
        try:
            removed = await asyncio.to_thread(
                subscriptions.unsubscribe,
                owner_user_id=owner,
                session_id=session_id,
                source_key=body.source_key,
            )
        except SourceError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        return SessionSubscriptionRemoved(
            session_id=session_id, source_key=body.source_key, removed=removed
        )

    @router.get("/agent-tasks/source-types", response_model=SourceTypeList)
    async def list_source_types(request: Request) -> SourceTypeList:
        """List the caller's source types in use, by name, each with its watcher."""
        user_id = require_user(request, auth_provider)
        return await asyncio.to_thread(source_type_list, user_id)

    @router.post(
        "/agent-tasks/source-types/{source_type}/watcher", response_model=SourceTypeResponse
    )
    async def assign_source_type(
        request: Request, source_type: str, body: AssignSourceTypeRequest
    ) -> SourceTypeResponse:
        """Assign a source type to one of the caller's watchers, in place of any it had.

        Only the caller or their current custodian session may assign. The
        fallback watcher takes no assignment: it checks the types that have none,
        and it stops tracking the items of a type once the type has a watcher.
        """
        user_id = require_user(request, auth_provider)
        await asyncio.to_thread(require_user_or_custodian, request, user_id)
        kind = _source_type(source_type)

        def assign() -> SourceTypeResponse:
            watcher = board.watchers.get(body.watcher_id, owner_user_id=user_id)
            if watcher is None:
                raise OmnigentError("watcher not found", code=ErrorCode.NOT_FOUND)
            if watcher.builtin:
                raise OmnigentError(
                    "the fallback watcher checks the types without a watcher; "
                    "leave the type unassigned instead",
                    code=ErrorCode.INVALID_INPUT,
                )
            subscriptions.assign_type(
                owner_user_id=user_id, source_type=kind, watcher_id=watcher.id
            )
            fallback = board.watchers.get_builtin(owner_user_id=user_id)
            if fallback is not None:
                board.watch_items.delete_source(fallback.id, owner_user_id=user_id, source=kind)
            return source_type_entry(user_id, kind)

        return await asyncio.to_thread(assign)

    @router.delete(
        "/agent-tasks/source-types/{source_type}/watcher", response_model=SourceTypeResponse
    )
    async def unassign_source_type(request: Request, source_type: str) -> SourceTypeResponse:
        """Leave a source type to the fallback watcher.

        Only the caller or their current custodian session may unassign.
        """
        user_id = require_user(request, auth_provider)
        await asyncio.to_thread(require_user_or_custodian, request, user_id)
        kind = _source_type(source_type)

        def unassign() -> SourceTypeResponse:
            subscriptions.unassign_type(owner_user_id=user_id, source_type=kind)
            return source_type_entry(user_id, kind)

        return await asyncio.to_thread(unassign)

    def require_user_or_custodian(request: Request, user_id: str | None) -> None:
        caller = runner_bound_caller(request, conversation_store)
        if caller is None:
            return
        custodian = (
            current_role_session(custodian_role, user_id=user_id, stores=sessions)
            if sessions is not None
            else None
        )
        if custodian is None or custodian.id != caller.id:
            raise OmnigentError(
                "only the custodian assigns source types to watchers",
                code=ErrorCode.FORBIDDEN,
            )

    def source_type_list(user_id: str | None) -> SourceTypeList:
        names = {
            watcher.id: watcher.name for watcher in board.watchers.list(owner_user_id=user_id)
        }
        return SourceTypeList(
            data=[
                SourceTypeResponse(
                    source_type=use.source_type,
                    watcher_id=use.watcher_id,
                    watcher_name=names.get(use.watcher_id) if use.watcher_id else None,
                    subscriptions=use.subscriptions,
                )
                for use in subscriptions.source_types(owner_user_id=user_id)
            ]
        )

    def source_type_entry(user_id: str | None, source_type: str) -> SourceTypeResponse:
        listed = source_type_list(user_id).data
        found = next((entry for entry in listed if entry.source_type == source_type), None)
        return found or SourceTypeResponse(
            source_type=source_type, watcher_id=None, watcher_name=None, subscriptions=0
        )

    return router


def _source_type(value: str) -> str:
    try:
        source_type = normalize_source_type(value)
    except SourceError as exc:
        raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
    if is_reserved_source_type(source_type):
        raise OmnigentError(
            f"the source type {source_type!r} is the server's own", code=ErrorCode.INVALID_INPUT
        )
    return source_type


def subscription_response(subscription: Subscription) -> SessionSubscriptionResponse:
    """Return the API form of a subscription."""
    return SessionSubscriptionResponse(
        id=subscription.id,
        session_id=subscription.session_id,
        source_type=subscription.source_type,
        source_key=subscription.source_key,
        note=subscription.note,
        url=subscription.url,
        created_at=subscription.created_at,
    )
