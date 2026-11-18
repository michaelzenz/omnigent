"""What the caller's GlobalHub watchers track (``/v1/agent-tasks/watchers/.../items``).

A watcher's own session lists, observes and finishes its items through the
``self`` routes; the runner's tunnel token proves which session calls. The
board UI lists the items of any of the caller's watchers. The ``self`` routes
come first, so the ``{watcher_id}`` route does not take ``self`` as an id.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from typing import Literal, cast

from fastapi import APIRouter, Query, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.role_sessions import RoleSessionStores
from omnigent.db.utils import now_epoch
from omnigent.entities import Conversation, Watcher, WatchItem
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._agent_task_callers import runner_bound_caller
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    FinishWatchItemsRequest,
    FinishWatchItemsResponse,
    ObserveWatchItemsRequest,
    ObserveWatchItemsResponse,
    WatchItemChange,
    WatchItemKey,
    WatchItemList,
    WatchItemObservation,
    WatchItemObservationResult,
    WatchItemResponse,
    WatchItemStatusFilter,
)
from omnigent.stores.subscription_store import (
    SourceError,
    is_reserved_source_type,
    normalize_source_key,
    normalize_source_type,
)
from omnigent.stores.watch_item_store import (
    WatchFinish,
    WatchItemLimitError,
    WatchObservation,
    WatchObservationResult,
)

_STATUSES: dict[str, tuple[str, ...]] = {
    "active": ("active",),
    "done": ("done",),
    "all": ("active", "done"),
}
# The most items in one page; a watcher can track any number of items.
_PAGE_MAX = 500


def create_agent_task_watch_items_router(
    board: BoardStores,
    sessions: RoleSessionStores,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for what the caller's GlobalHub watchers track.

    :param board: Holds the watchers and their items.
    :param sessions: Finds the session behind a watcher's call.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    items = board.watch_items

    def calling_watcher(request: Request, user_id: str | None) -> tuple[Watcher, Conversation]:
        session = runner_bound_caller(request, sessions.conversation_store)
        if session is None:
            raise OmnigentError(
                "only a watcher's own session can use its tracker", code=ErrorCode.FORBIDDEN
            )
        watcher = board.watchers.get_by_conversation_id(session.id, owner_user_id=user_id)
        if watcher is None:
            raise OmnigentError(
                "the calling session is not one of your watchers", code=ErrorCode.FORBIDDEN
            )
        return watcher, session

    def listed(
        watcher_id: str,
        user_id: str | None,
        statuses: Collection[str],
        *,
        source: str | None = None,
        limit: int,
        after: str | None,
    ) -> WatchItemList:
        page = items.list(
            watcher_id,
            owner_user_id=user_id,
            statuses=statuses,
            source=source,
            limit=limit,
            after=after,
        )
        return WatchItemList(
            data=[_item(item) for item in page.data],
            first_id=page.first_id,
            last_id=page.last_id,
            has_more=page.has_more,
            total=items.count(watcher_id, owner_user_id=user_id, statuses=statuses),
        )

    @router.get("/agent-tasks/watchers/self/items", response_model=WatchItemList)
    async def list_own_items(
        request: Request,
        status: WatchItemStatusFilter = "active",
        source: str | None = None,
        limit: int = Query(default=100, ge=1, le=_PAGE_MAX),
        after: str | None = None,
    ) -> WatchItemList:
        """List a page of the calling watcher's items, in the order it started to track them.

        Pass the page's ``last_id`` as ``after`` for the next page while
        ``has_more`` is true.
        """
        user_id = require_user(request, auth_provider)

        def load() -> WatchItemList:
            watcher, _ = calling_watcher(request, user_id)
            return listed(
                watcher.id,
                user_id,
                _STATUSES[status],
                source=source,
                limit=limit,
                after=after,
            )

        return await asyncio.to_thread(load)

    @router.post(
        "/agent-tasks/watchers/self/items/observe", response_model=ObserveWatchItemsResponse
    )
    async def observe_own_items(
        request: Request, body: ObserveWatchItemsRequest
    ) -> ObserveWatchItemsResponse:
        """Save what the calling watcher sees now; each change records task events.

        A new item is a silent baseline unless ``announce`` is set. Each session
        that follows the item's key gets an event; when no session follows it,
        the broker gets one. A done item whose offsets change is reopened. Every
        item is checked before anything is saved, so a refused call changes
        nothing.
        """
        user_id = require_user(request, auth_provider)
        observations = [_observation(item) for item in body.items]
        _each_key_once([(item.source, item.source_key) for item in observations])

        def observe() -> list[WatchObservationResult]:
            watcher, session = calling_watcher(request, user_id)
            try:
                return items.observe(watcher, observations, session_id=session.id, now=now_epoch())
            except WatchItemLimitError as exc:
                raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc

        results = await asyncio.to_thread(observe)
        return ObserveWatchItemsResponse(data=[_result(result) for result in results])

    @router.post("/agent-tasks/watchers/self/items/done", response_model=FinishWatchItemsResponse)
    async def finish_own_items(
        request: Request, body: FinishWatchItemsRequest
    ) -> FinishWatchItemsResponse:
        """Mark the calling watcher's items done, and tell the sessions that follow them.

        Each session that follows a newly done item gets a ``watch.done`` event.
        A done item is reopened when an observation changes its offsets, and is
        deleted 30 days after it was marked done.
        """
        user_id = require_user(request, auth_provider)
        finished = [
            WatchFinish(
                source=_source_type(item.source),
                source_key=_source_key(item.source_key),
                summary=item.summary.strip() if item.summary else None,
            )
            for item in body.items
        ]
        _each_key_once([(item.source, item.source_key) for item in finished])

        def finish() -> list[tuple[str, str]]:
            watcher, session = calling_watcher(request, user_id)
            return items.finish(watcher, finished, session_id=session.id, now=now_epoch())

        missing = await asyncio.to_thread(finish)
        return FinishWatchItemsResponse(
            done=len(finished) - len(missing),
            missing=[WatchItemKey(source=source, source_key=key) for source, key in missing],
        )

    @router.get("/agent-tasks/watchers/{watcher_id}/items", response_model=WatchItemList)
    async def list_watcher_items(
        request: Request,
        watcher_id: str,
        status: WatchItemStatusFilter = "active",
        limit: int = Query(default=100, ge=1, le=_PAGE_MAX),
        after: str | None = None,
    ) -> WatchItemList:
        """List a page of one of the caller's watchers' items, in the order of its own list."""
        user_id = require_user(request, auth_provider)

        def load() -> WatchItemList:
            if board.watchers.get(watcher_id, owner_user_id=user_id) is None:
                raise OmnigentError("watcher not found", code=ErrorCode.NOT_FOUND)
            return listed(watcher_id, user_id, _STATUSES[status], limit=limit, after=after)

        return await asyncio.to_thread(load)

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


def _source_key(value: str) -> str:
    try:
        return normalize_source_key(value)
    except SourceError as exc:
        raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc


def _each_key_once(keys: list[tuple[str, str]]) -> None:
    if len(set(keys)) != len(keys):
        raise OmnigentError(
            "send each source and source_key once per call", code=ErrorCode.INVALID_INPUT
        )


def _observation(item: WatchItemObservation) -> WatchObservation:
    return WatchObservation(
        source=_source_type(item.source),
        source_key=_source_key(item.source_key),
        offsets=item.offsets,
        title=item.title.strip() if item.title is not None else None,
        summary=item.summary,
        details=item.details,
        announce=item.announce,
    )


def _item(item: WatchItem) -> WatchItemResponse:
    return WatchItemResponse(
        id=item.id,
        source=item.source,
        source_key=item.source_key,
        title=item.title,
        offsets=item.offsets,
        status=cast(Literal["active", "done"], item.status),
        created_at=item.created_at,
        checked_at=item.checked_at,
        changed_at=item.changed_at,
        done_at=item.done_at,
    )


def _result(result: WatchObservationResult) -> WatchItemObservationResult:
    return WatchItemObservationResult(
        source=result.source,
        source_key=result.source_key,
        outcome=result.outcome,
        changes=[
            WatchItemChange(offset=change.offset, before=change.before, after=change.after)
            for change in result.changes
        ],
        event_ids=list(result.event_ids),
    )
