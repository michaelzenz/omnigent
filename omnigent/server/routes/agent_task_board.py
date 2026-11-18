"""The caller's GlobalHub board as task cards (``/v1/agent-tasks/board``).

The router also lists the sessions of the board's workers
(``/v1/agent-tasks/worker-sessions``). ``/agent-tasks/{task_id}`` also matches
both paths, so this router must be included before the tasks router.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import cast

from fastapi import APIRouter, Query, Request

from omnigent.agent_tasks.board_search import (
    BoardMatch,
    last_active_by_task,
    query_words,
    rank_with_vectors,
    search_board,
)
from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.task_search_index import TaskSearchIndex
from omnigent.entities import LIVE_TASK_STATES, SESSION_EVENT_SOURCE, Task, TaskAsset, Worker
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.agent_task_delivery import SessionSendDeps
from omnigent.server.agent_task_session_states import read_session_states
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes.agent_task_assets import asset_response
from omnigent.server.routes.agent_task_workers import worker_response
from omnigent.server.schemas import (
    AgentTaskBoard,
    AgentTaskBoardCard,
    AgentTaskBoardMatch,
    AgentTaskBoardSession,
    AgentTaskBoardWorker,
    AgentTaskBoardWorkerSession,
    AgentTaskBoardWorkerSessions,
    AgentTaskState,
)
from omnigent.stores import ConversationStore
from omnigent.stores.task_store import TaskStore
from omnigent.util.uuid import InvalidUuidError, uuid_to_bytes

_logger = logging.getLogger(__name__)

#: The most cards one board page returns.
MAX_BOARD_PAGE = 100
#: The ranks a board cursor may hold: the range of the 64-bit rank column.
QUEUE_RANK_RANGE = range(-(2**63), 2**63)
#: The longest query a board search reads.
MAX_BOARD_QUERY_CHARS = 200
#: The longest cursor a board read takes.
MAX_BOARD_CURSOR_CHARS = 80


def create_agent_task_board_router(
    board: BoardStores,
    conversation_store: ConversationStore,
    session_deps: SessionSendDeps,
    auth_provider: AuthProvider | None = None,
    search_index: TaskSearchIndex | None = None,
) -> APIRouter:
    """Build the router for the caller's board cards.

    :param board: The board's tasks, workers and assets.
    :param conversation_store: Holds the sessions that workers wrap.
    :param session_deps: Reads runner liveness, so a worker's turn whose runner
        is gone settles before its card shows it.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :param search_index: Finds the tasks related to a query, or ``None`` for a
        search by keywords only.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    async def read_sessions(
        workers: Sequence[Worker], user_id: str | None
    ) -> dict[str, AgentTaskBoardSession]:
        if not workers:
            return {}
        found = await asyncio.to_thread(
            conversation_store.get_conversations,
            list(dict.fromkeys(worker.session_id for worker in workers)),
        )
        return await read_session_states(
            list(found.values()),
            user_id=user_id,
            conversation_store=conversation_store,
            deps=session_deps,
        )

    @router.get("/agent-tasks/board", response_model=AgentTaskBoard)
    async def read_board(
        request: Request,
        limit: int = Query(default=20, ge=1, le=MAX_BOARD_PAGE),
        q: str = Query(default="", max_length=MAX_BOARD_QUERY_CHARS),
        after: str | None = Query(default=None, max_length=MAX_BOARD_CURSOR_CHARS),
        before: str | None = Query(default=None, max_length=MAX_BOARD_CURSOR_CHARS),
    ) -> AgentTaskBoard:
        """Read a page of ``limit`` live tasks on the caller's board as cards, in board order.

        ``after`` is the ``next_cursor`` of the page above, and ``before`` the
        ``prev_cursor`` of the page below; without either, the page starts at
        the top of the board. A read past either end of the board returns the
        page at that end. With a query ``q``, the pages hold the live tasks
        whose cards hold any of its words instead, best match first, as
        ``board_search`` ranks them. With vector search, they also hold the
        tasks near the query in meaning, all in one ranking. A worker whose
        session no longer exists is left off its card. The count of tasks
        without a manager covers the whole board.
        """
        user_id = require_user(request, auth_provider)
        if after is not None and before is not None:
            raise OmnigentError("pass after or before, not both", code=ErrorCode.INVALID_INPUT)
        words = query_words(q)
        matches: dict[str, BoardMatch] = {}
        if words:
            after_offset, before_offset = _search_offset(after), _search_offset(before)
            query_vector = (
                await search_index.query_vector(q, owner_user_id=user_id)
                if search_index is not None
                else None
            )
            found, next_cursor, prev_cursor = await asyncio.to_thread(
                _search,
                board,
                conversation_store,
                user_id,
                words,
                limit,
                after_offset,
                before_offset,
                search_index,
                query_vector,
            )
            tasks = [match.task for match in found]
            matches = {match.task.id: match for match in found}
        else:
            tasks, next_cursor, prev_cursor = await asyncio.to_thread(
                _live_tasks,
                board.tasks,
                user_id,
                limit,
                _board_cursor_position(after),
                _board_cursor_position(before),
            )
        task_ids = [task.id for task in tasks]
        workers, assets, unmanaged_count = await asyncio.gather(
            asyncio.to_thread(board.workers.list_for_tasks, task_ids, owner_user_id=user_id),
            asyncio.to_thread(board.assets.list_for_tasks, task_ids, owner_user_id=user_id),
            asyncio.to_thread(
                board.tasks.count, owner_user_id=user_id, states=LIVE_TASK_STATES, unmanaged=True
            ),
        )
        worker_sessions = await read_sessions(workers, user_id)
        workers_by_task: dict[str, list[AgentTaskBoardWorker]] = defaultdict(list)
        for worker in workers:
            session = worker_sessions.get(worker.session_id)
            if session is not None:
                workers_by_task[worker.task_id].append(
                    AgentTaskBoardWorker(**worker_response(worker).model_dump(), session=session)
                )
        assets_by_task: dict[str, list[TaskAsset]] = defaultdict(list)
        for asset in assets:
            assets_by_task[asset.task_id].append(asset)
        return AgentTaskBoard(
            data=[
                _card(
                    task, workers_by_task[task.id], assets_by_task[task.id], matches.get(task.id)
                )
                for task in tasks
            ],
            has_more=next_cursor is not None,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            unmanaged_count=unmanaged_count,
        )

    @router.get("/agent-tasks/worker-sessions", response_model=AgentTaskBoardWorkerSessions)
    async def read_worker_sessions(request: Request) -> AgentTaskBoardWorkerSessions:
        """Read the session of each worker on the caller's live tasks, each session once.

        The Global Hub link reads them to show a dot while one of them has a
        finished turn that the caller has not seen. A worker whose session no
        longer exists is left out.
        """
        user_id = require_user(request, auth_provider)
        tasks = await asyncio.to_thread(
            board.tasks.list, owner_user_id=user_id, states=LIVE_TASK_STATES
        )
        workers = await asyncio.to_thread(
            board.workers.list_for_tasks, [task.id for task in tasks], owner_user_id=user_id
        )
        sessions = await read_sessions(workers, user_id)
        return AgentTaskBoardWorkerSessions(
            data=[
                AgentTaskBoardWorkerSession(session_id=session_id, session=session)
                for session_id, session in sessions.items()
            ]
        )

    return router


def _search(
    board: BoardStores,
    conversations: ConversationStore,
    owner_user_id: str | None,
    words: list[str],
    limit: int,
    after: int | None,
    before: int | None,
    search_index: TaskSearchIndex | None = None,
    query_vector: list[float] | None = None,
) -> tuple[list[BoardMatch], str | None, str | None]:
    """
    Return a page of the live tasks that match a query, and its two cursors.

    With a query vector, the keyword matches and the tasks near the query in
    meaning share one ranking. When the vectors cannot be read, the search
    keeps the keyword matches in their own order.
    """
    tasks = board.tasks.list(owner_user_id=owner_user_id, states=LIVE_TASK_STATES)
    task_ids = [task.id for task in tasks]
    workers = board.workers.list_for_tasks(task_ids, owner_user_id=owner_user_id)
    sessions = conversations.get_conversations(
        list(dict.fromkeys(worker.session_id for worker in workers))
    )
    # As on the card: a worker whose session no longer exists is not shown.
    shown = [worker for worker in workers if worker.session_id in sessions]
    worker_activity = {worker.id: sessions[worker.session_id].updated_at for worker in shown}
    found = search_board(
        tasks,
        words,
        assets=board.assets.list_for_tasks(task_ids, owner_user_id=owner_user_id),
        workers=shown,
        worker_labels={
            worker.id: worker.title or sessions[worker.session_id].title or "" for worker in shown
        },
        worker_activity=worker_activity,
    )
    if search_index is not None and query_vector is not None:
        found = _rank_with_vectors(
            board,
            search_index,
            owner_user_id,
            query_vector,
            words,
            tasks,
            found,
            shown,
            worker_activity,
        )
    if before is None:
        start = after or 0
        if start >= len(found):
            start = max(len(found) - limit, 0)
        end = start + limit
    else:
        end = limit if before <= limit else min(before, len(found))
        start = max(end - limit, 0)
    next_cursor = str(end) if len(found) > end else None
    return found[start:end], next_cursor, str(start) if start > 0 else None


def _rank_with_vectors(
    board: BoardStores,
    search_index: TaskSearchIndex,
    owner_user_id: str | None,
    query_vector: list[float],
    words: Sequence[str],
    tasks: Sequence[Task],
    keyword_matches: list[BoardMatch],
    shown: Sequence[Worker],
    worker_activity: dict[str, int],
) -> list[BoardMatch]:
    """Rank keyword matches and tasks near the query together, or keep the keyword order."""
    try:
        scores = search_index.task_scores(owner_user_id=owner_user_id, vector=query_vector)
        routed_at = board.events.latest_routes(
            SESSION_EVENT_SOURCE,
            {
                worker.session_id
                for task_scores in scores.values()
                for worker in task_scores.workers
            },
            owner_user_id=owner_user_id,
        )
    except Exception:
        _logger.exception("The board search could not read its vectors")
        return keyword_matches
    if not scores:
        return keyword_matches
    return rank_with_vectors(
        tasks,
        keyword_matches,
        words,
        scores=scores,
        min_score=search_index.min_score,
        shown_worker_ids={worker.id for worker in shown},
        last_active=last_active_by_task(tasks, shown, worker_activity),
        routed_at=routed_at,
    )


def _live_tasks(
    tasks: TaskStore,
    owner_user_id: str | None,
    limit: int,
    after: tuple[int, str] | None,
    before: tuple[int, str] | None,
) -> tuple[list[Task], str | None, str | None]:
    """Return a page of the owner's live tasks in board order, and its two cursors.

    A ``before`` read that reaches the top of the board returns the first page,
    and an ``after`` read with no task after its cursor returns the page before
    the cursor, so a page read again after the board changed is not empty.
    """
    if before is not None:
        nearest = _live_tasks_beside(tasks, owner_user_id, limit + 1, before=before)[-limit - 1 :]
        if len(nearest) <= limit:
            return _live_tasks(tasks, owner_user_id, limit, None, None)
        page = nearest[-limit:]
        more_after = bool(
            _live_tasks_beside(tasks, owner_user_id, 1, after=_board_position(page[-1]))
        )
        return page, _board_cursor(page[-1]) if more_after else None, _board_cursor(page[0])
    nearest = _live_tasks_beside(tasks, owner_user_id, limit + 1, after=after)[: limit + 1]
    if not nearest and after is not None:
        return _live_tasks(tasks, owner_user_id, limit, None, after)
    page = nearest[:limit]
    more_before = (
        after is not None
        and bool(page)
        and bool(_live_tasks_beside(tasks, owner_user_id, 1, before=_board_position(page[0])))
    )
    return (
        page,
        _board_cursor(page[-1]) if len(nearest) > limit else None,
        _board_cursor(page[0]) if more_before else None,
    )


def _live_tasks_beside(
    tasks: TaskStore,
    owner_user_id: str | None,
    limit: int,
    *,
    after: tuple[int, str] | None = None,
    before: tuple[int, str] | None = None,
) -> list[Task]:
    """Return each live state's ``limit`` tasks nearest to a board position, in board order."""
    # The board index orders one state at a time, so read each state and merge.
    per_state = [
        tasks.list(
            owner_user_id=owner_user_id, states=[state], limit=limit, after=after, before=before
        )
        for state in LIVE_TASK_STATES
    ]
    return list(heapq.merge(*per_state, key=_board_position, reverse=True))


def _board_position(task: Task) -> tuple[int, str]:
    return task.queue_rank, task.id


def _board_cursor(task: Task) -> str:
    return f"{task.queue_rank}:{task.id}"


def _board_cursor_position(cursor: str | None) -> tuple[int, str] | None:
    """Read a board cursor, the ``queue_rank:id`` of the card next to the page."""
    if cursor is None:
        return None
    rank_text, _, task_id = cursor.partition(":")
    try:
        uuid_to_bytes(task_id)
        rank = int(rank_text)
    except (InvalidUuidError, ValueError) as exc:
        raise OmnigentError("invalid board cursor", code=ErrorCode.INVALID_INPUT) from exc
    if rank not in QUEUE_RANK_RANGE:
        raise OmnigentError("invalid board cursor", code=ErrorCode.INVALID_INPUT)
    return rank, task_id


def _search_offset(cursor: str | None) -> int | None:
    """Read a search cursor, the index of the match next to the page."""
    if cursor is None:
        return None
    if not (cursor.isascii() and cursor.isdigit()):
        raise OmnigentError("invalid search cursor", code=ErrorCode.INVALID_INPUT)
    return int(cursor)


def _card(
    task: Task,
    workers: list[AgentTaskBoardWorker],
    assets: list[TaskAsset],
    match: BoardMatch | None,
) -> AgentTaskBoardCard:
    return AgentTaskBoardCard(
        id=task.id,
        manager_id=task.manager_id,
        title=task.title,
        description=task.description,
        state=cast(AgentTaskState, task.state),
        priority=task.priority,
        queue_rank=task.queue_rank,
        created_at=task.created_at,
        updated_at=task.updated_at,
        workers=sorted(workers, key=_worker_position),
        assets=[asset_response(asset) for asset in assets],
        match=(
            AgentTaskBoardMatch(
                hits=match.hits,
                asset_ids=list(match.asset_ids),
                worker_ids=list(match.worker_ids),
                title_match=match.title_match,
            )
            if match is not None
            else None
        ),
    )


def _worker_position(worker: AgentTaskBoardWorker) -> tuple[bool, int, str]:
    """Busy sessions first, then the most recently active."""
    session = worker.session
    busy = session.status in ("running", "waiting") or session.background_work_running
    return not busy, -session.updated_at, worker.id
