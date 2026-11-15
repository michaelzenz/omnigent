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

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import LIVE_TASK_STATES, Conversation, Task, TaskAsset, Worker
from omnigent.runtime import pending_elicitations
from omnigent.server.agent_task_delivery import SessionSendDeps, settle_orphaned_turns
from omnigent.server.auth import AuthProvider
from omnigent.server.routes import sessions as session_routes
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes.agent_task_assets import asset_response
from omnigent.server.routes.agent_task_workers import worker_response
from omnigent.server.schemas import (
    AgentTaskBoard,
    AgentTaskBoardCard,
    AgentTaskBoardSession,
    AgentTaskBoardWorker,
    AgentTaskState,
)
from omnigent.stores import ConversationStore
from omnigent.stores.task_store import TaskStore

#: The most cards one board read returns.
MAX_BOARD_CARDS = 1000


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
        conversations = [conv for conv in found.values() if conv.agent_id is not None]
        # A settled session publishes its new status, which the rows below read.
        await settle_orphaned_turns(conversations, deps=session_deps)
        ids = [conv.id for conv in conversations]
        children = await asyncio.to_thread(
            conversation_store.list_child_conversation_ids_by_parent, ids
        )
        pending = pending_elicitations.counts_for(ids)
        return {
            conv.id: _session_state(conv, user_id, pending[conv.id], children.get(conv.id, []))
            for conv in conversations
        }

    @router.get("/agent-tasks/board", response_model=AgentTaskBoard)
    async def read_board(
        request: Request, limit: int = Query(default=20, ge=1, le=MAX_BOARD_CARDS)
    ) -> AgentTaskBoard:
        """Read the first ``limit`` live tasks on the caller's board as cards, in board order.

        A worker whose session no longer exists is left off its card.
        """
        user_id = require_user(request, auth_provider)
        tasks, has_more = await asyncio.to_thread(_live_tasks, board.tasks, user_id, limit)
        task_ids = [task.id for task in tasks]
        workers, assets = await asyncio.gather(
            asyncio.to_thread(board.workers.list_for_tasks, task_ids, owner_user_id=user_id),
            asyncio.to_thread(board.assets.list_for_tasks, task_ids, owner_user_id=user_id),
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
                _card(task, workers_by_task[task.id], assets_by_task[task.id]) for task in tasks
            ],
            has_more=has_more,
        )

    return router


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


def _session_state(
    conv: Conversation, user_id: str | None, pending_count: int, child_session_ids: list[str]
) -> AgentTaskBoardSession:
    # Main's session-list row, so a worker reads as it does in the sidebar.
    row = session_routes._build_session_list_item(
        conv,
        agent_names_by_id={},
        grants=[],
        user_id=user_id,
        user_is_admin=False,
        permissions_enabled=False,
        pending_count=pending_count,
        child_session_ids=child_session_ids,
        comments_fingerprint=None,
    )
    return AgentTaskBoardSession(
        title=row.title,
        status=row.status,
        pending_elicitations_count=row.pending_elicitations_count,
        background_work_running=row.background_work_running,
        blocked_on=row.blocked_on,
        updated_at=row.updated_at,
    )


def _card(
    task: Task, workers: list[AgentTaskBoardWorker], assets: list[TaskAsset]
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
    )


def _worker_position(worker: AgentTaskBoardWorker) -> tuple[bool, int, str]:
    """Busy sessions first, then the most recently active."""
    session = worker.session
    busy = session.status in ("running", "waiting") or session.background_work_running
    return not busy, -session.updated_at, worker.id
