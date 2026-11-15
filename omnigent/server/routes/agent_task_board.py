"""The caller's GlobalHub board as task cards (``/v1/agent-tasks/board``).

``/agent-tasks/{task_id}`` also matches ``/agent-tasks/board``, so this router
must be included before the tasks router.
"""

from __future__ import annotations

import asyncio
import heapq
from collections import defaultdict
from collections.abc import Sequence
from itertools import islice
from typing import cast

from fastapi import APIRouter, Query, Request

from omnigent.agent_tasks.board_search import BoardMatch, query_words, search_board
from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import LIVE_TASK_STATES, Task, TaskAsset, Worker
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
    AgentTaskState,
)
from omnigent.stores import ConversationStore
from omnigent.stores.task_store import TaskStore

#: The most cards one board read returns.
MAX_BOARD_CARDS = 1000
#: The longest query a board search reads.
MAX_BOARD_QUERY_CHARS = 200


def create_agent_task_board_router(
    board: BoardStores,
    conversation_store: ConversationStore,
    session_deps: SessionSendDeps,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the caller's board cards.

    :param board: The board's tasks, workers and assets.
    :param conversation_store: Holds the sessions that workers wrap.
    :param session_deps: Reads runner liveness, so a worker's turn whose runner
        is gone settles before its card shows it.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
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
        limit: int = Query(default=20, ge=1, le=MAX_BOARD_CARDS),
        q: str = Query(default="", max_length=MAX_BOARD_QUERY_CHARS),
    ) -> AgentTaskBoard:
        """Read the first ``limit`` live tasks on the caller's board as cards, in board order.

        With a query ``q``, read the first ``limit`` live tasks whose cards hold
        any of its words instead, best match first, as ``board_search`` ranks
        them. A worker whose session no longer exists is left off its card. The
        count of tasks without a manager covers the whole board.
        """
        user_id = require_user(request, auth_provider)
        words = query_words(q)
        matches: dict[str, BoardMatch] = {}
        if words:
            found, has_more = await asyncio.to_thread(
                _search, board, conversation_store, user_id, words, limit
            )
            tasks = [match.task for match in found]
            matches = {match.task.id: match for match in found}
        else:
            tasks, has_more = await asyncio.to_thread(_live_tasks, board.tasks, user_id, limit)
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
            has_more=has_more,
            unmanaged_count=unmanaged_count,
        )

    return router


def _search(
    board: BoardStores,
    conversations: ConversationStore,
    owner_user_id: str | None,
    words: list[str],
    limit: int,
) -> tuple[list[BoardMatch], bool]:
    """Return the first ``limit`` live tasks whose cards hold a query word, and whether more do."""
    tasks = board.tasks.list(owner_user_id=owner_user_id, states=LIVE_TASK_STATES)
    task_ids = [task.id for task in tasks]
    workers = board.workers.list_for_tasks(task_ids, owner_user_id=owner_user_id)
    sessions = conversations.get_conversations(
        list(dict.fromkeys(worker.session_id for worker in workers))
    )
    # As on the card: a worker whose session no longer exists is not shown.
    shown = [worker for worker in workers if worker.session_id in sessions]
    found = search_board(
        tasks,
        words,
        assets=board.assets.list_for_tasks(task_ids, owner_user_id=owner_user_id),
        workers=shown,
        worker_labels={
            worker.id: worker.title or sessions[worker.session_id].title or "" for worker in shown
        },
        worker_activity={worker.id: sessions[worker.session_id].updated_at for worker in shown},
    )
    return found[:limit], len(found) > limit


def _live_tasks(
    tasks: TaskStore, owner_user_id: str | None, limit: int
) -> tuple[list[Task], bool]:
    """Return the owner's first ``limit`` live tasks in board order, and whether more exist."""
    # The board index orders one state at a time, so read each state and merge.
    per_state = [
        tasks.list(owner_user_id=owner_user_id, states=[state], limit=limit + 1)
        for state in LIVE_TASK_STATES
    ]
    first = list(islice(heapq.merge(*per_state, key=_board_position, reverse=True), limit + 1))
    return first[:limit], len(first) > limit


def _board_position(task: Task) -> tuple[int, str]:
    return task.queue_rank, task.id


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
        goal=task.goal,
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
