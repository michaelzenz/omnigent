"""Sessions working on the caller's GlobalHub tasks (workers)."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request
from sqlalchemy.exc import IntegrityError

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import Worker
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_OWNER, AuthProvider
from omnigent.server.routes._auth_helpers import require_access, require_user
from omnigent.server.schemas import (
    AdoptAgentTaskSessionRequest,
    AgentTaskSessionAdoption,
    AgentTaskWorkerDeletedResponse,
    AgentTaskWorkerList,
    AgentTaskWorkerResponse,
)
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore


def create_agent_task_workers_router(
    board: BoardStores,
    conversation_store: ConversationStore,
    permission_store: PermissionStore | None = None,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the sessions working on the caller's tasks.

    :param board: The board's tasks and workers.
    :param conversation_store: Holds the sessions that workers wrap.
    :param permission_store: Checks that the caller owns an adopted session;
        ``None`` on servers without permissions.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    tasks = board.tasks
    workers = board.workers

    async def require_owned_task(task_id: str, user_id: str | None) -> None:
        if await asyncio.to_thread(tasks.get, task_id, owner_user_id=user_id) is None:
            raise OmnigentError("task not found", code=ErrorCode.NOT_FOUND)

    async def require_owned_session(session_id: str, user_id: str | None) -> None:
        if await asyncio.to_thread(conversation_store.get_conversation, session_id) is None:
            raise OmnigentError("session not found", code=ErrorCode.NOT_FOUND)
        await require_access(
            user_id, session_id, LEVEL_OWNER, permission_store, conversation_store
        )

    @router.post(
        "/agent-tasks/sessions/{session_id}/adopt", response_model=AgentTaskSessionAdoption
    )
    async def adopt_session(
        request: Request, session_id: str, body: AdoptAgentTaskSessionRequest
    ) -> AgentTaskSessionAdoption:
        """Attach one of the caller's sessions to one of their tasks.

        A session has one worker per task: adopting it again only retitles that
        worker.
        """
        user_id = require_user(request, auth_provider)
        await require_owned_task(body.task_id, user_id)
        await require_owned_session(session_id, user_id)

        def adopt() -> tuple[Worker, bool]:
            existing = workers.get_for_task_and_session(
                body.task_id, session_id, owner_user_id=user_id
            )
            if existing is None:
                try:
                    created = workers.create(
                        uuid.uuid4().hex,
                        owner_user_id=user_id,
                        task_id=body.task_id,
                        session_id=session_id,
                        title=body.title,
                    )
                    return created, True
                except IntegrityError:
                    # A concurrent adoption attached the session first.
                    existing = workers.get_for_task_and_session(
                        body.task_id, session_id, owner_user_id=user_id
                    )
                    if existing is None:
                        raise
            if body.title is None or body.title == existing.title:
                return existing, False
            retitled = workers.update(existing.id, owner_user_id=user_id, title=body.title)
            return retitled or existing, False

        worker, created = await asyncio.to_thread(adopt)
        return AgentTaskSessionAdoption(
            session_id=session_id,
            task_id=body.task_id,
            worker_id=worker.id,
            already_bound=not created,
        )

    @router.get("/agent-tasks/{task_id}/workers", response_model=AgentTaskWorkerList)
    async def list_task_workers(request: Request, task_id: str) -> AgentTaskWorkerList:
        """List the sessions working on one of the caller's tasks, in attach order."""
        user_id = require_user(request, auth_provider)
        await require_owned_task(task_id, user_id)
        found = await asyncio.to_thread(workers.list_for_tasks, [task_id], owner_user_id=user_id)
        return AgentTaskWorkerList(data=[worker_response(worker) for worker in found])

    @router.post(
        "/task-workers/{worker_id}/untrack", response_model=AgentTaskWorkerDeletedResponse
    )
    async def untrack_worker(request: Request, worker_id: str) -> AgentTaskWorkerDeletedResponse:
        """Remove one of the caller's workers from its task; the session keeps running."""
        user_id = require_user(request, auth_provider)
        if not await asyncio.to_thread(workers.delete, worker_id, owner_user_id=user_id):
            raise OmnigentError("worker not found", code=ErrorCode.NOT_FOUND)
        return AgentTaskWorkerDeletedResponse(id=worker_id)

    return router


def worker_response(worker: Worker) -> AgentTaskWorkerResponse:
    return AgentTaskWorkerResponse(
        id=worker.id,
        task_id=worker.task_id,
        session_id=worker.session_id,
        title=worker.title,
        created_at=worker.created_at,
        updated_at=worker.updated_at,
    )
