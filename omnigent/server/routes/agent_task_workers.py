"""Sessions working on the caller's GlobalHub tasks (workers)."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.worker_subscriptions import follow_workers
from omnigent.entities import Task, Worker
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.agent_queue_dispatcher import WORKER_MESSAGE_KIND
from omnigent.server.auth import LEVEL_OWNER, AuthProvider
from omnigent.server.routes._auth_helpers import require_access, require_user
from omnigent.server.routes._content_type import require_json_content_type
from omnigent.server.routes._origin import require_trusted_origin
from omnigent.server.schemas import (
    AdoptAgentTaskSessionRequest,
    AgentTaskSessionAdoption,
    AgentTaskWorkerList,
    AgentTaskWorkerMessageQueued,
    AgentTaskWorkerMessageRequest,
    AgentTaskWorkerResponse,
    StartAgentTaskWorkerRequest,
    StartedAgentTaskWorker,
)
from omnigent.server.session_orchestration import require_named_source_private
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.worker_store import WorkerStore

if TYPE_CHECKING:
    from omnigent.server.agent_task_worker_sessions import WorkerSessionStarter


def create_agent_task_workers_router(
    board: BoardStores,
    conversation_store: ConversationStore,
    permission_store: PermissionStore | None = None,
    auth_provider: AuthProvider | None = None,
    starter: WorkerSessionStarter | None = None,
) -> APIRouter:
    """Build the router for the sessions working on the caller's tasks.

    :param board: The board's tasks and workers.
    :param conversation_store: Holds the sessions that workers wrap.
    :param permission_store: Checks that the caller owns an adopted session;
        ``None`` on servers without permissions.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :param starter: Starts sessions for tasks; ``None`` on servers without
        GlobalHub settings, which also cannot deliver messages to workers.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    tasks = board.tasks
    workers = board.workers

    async def require_owned_task(task_id: str, user_id: str | None) -> Task:
        task = await asyncio.to_thread(tasks.get, task_id, owner_user_id=user_id)
        if task is None:
            raise OmnigentError("task not found", code=ErrorCode.NOT_FOUND)
        return task

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
        worker. The task's manager follows the session.
        """
        user_id = require_user(request, auth_provider)
        task = await require_owned_task(body.task_id, user_id)
        await require_owned_session(session_id, user_id)
        worker, created = await asyncio.to_thread(
            adopt_worker,
            board,
            owner_user_id=user_id,
            task=task,
            session_id=session_id,
            title=body.title,
        )
        return AgentTaskSessionAdoption(
            session_id=session_id,
            task_id=body.task_id,
            worker_id=worker.id,
            already_bound=not created,
        )

    @router.post(
        "/agent-tasks/{task_id}/workers/start",
        status_code=201,
        response_model=StartedAgentTaskWorker,
        dependencies=[Depends(require_json_content_type), Depends(require_trusted_origin)],
    )
    async def start_worker(
        request: Request, task_id: str, body: StartAgentTaskWorkerRequest
    ) -> StartedAgentTaskWorker:
        """Start a session on one of the caller's hosts to work on one of their tasks.

        The session is the task's worker before its first message, which goes
        out in the background.
        """
        user_id = require_user(request, auth_provider)
        if starter is None:
            raise OmnigentError(
                "Starting sessions for tasks needs GlobalHub settings on this server",
                code=ErrorCode.INVALID_INPUT,
            )
        return await starter.start(request, task_id, body, user_id=user_id)

    @router.post(
        "/task-workers/{worker_id}/messages",
        status_code=202,
        response_model=AgentTaskWorkerMessageQueued,
        dependencies=[Depends(require_json_content_type), Depends(require_trusted_origin)],
    )
    async def message_worker(
        request: Request, worker_id: str, body: AgentTaskWorkerMessageRequest
    ) -> AgentTaskWorkerMessageQueued:
        """Queue a message for the session of one of the caller's workers.

        The message goes in as the caller's message when the session is free, and
        starts a turn; the messages for one session go in one at a time, in order.
        A request from an agent must come from a private session, as for a start.
        """
        user_id = require_user(request, auth_provider)
        if starter is None:
            raise OmnigentError(
                "Messaging workers needs GlobalHub settings on this server",
                code=ErrorCode.INVALID_INPUT,
            )
        await asyncio.to_thread(
            require_named_source_private, request, user_id, conversation_store, permission_store
        )
        worker = await asyncio.to_thread(workers.get, worker_id, owner_user_id=user_id)
        if worker is None:
            raise OmnigentError("worker not found", code=ErrorCode.NOT_FOUND)
        session = await asyncio.to_thread(conversation_store.get_conversation, worker.session_id)
        if session is None or session.archived:
            raise OmnigentError("the worker's session no longer exists", code=ErrorCode.NOT_FOUND)
        item_id = uuid.uuid4().hex
        await asyncio.to_thread(
            board.queue.enqueue,
            item_id,
            owner_user_id=user_id,
            target_session_id=worker.session_id,
            kind=WORKER_MESSAGE_KIND,
            payload={"worker_id": worker.id, "message": body.message},
        )
        return AgentTaskWorkerMessageQueued(
            id=item_id, worker_id=worker.id, session_id=worker.session_id
        )

    @router.get("/agent-tasks/{task_id}/workers", response_model=AgentTaskWorkerList)
    async def list_task_workers(request: Request, task_id: str) -> AgentTaskWorkerList:
        """List the sessions working on one of the caller's tasks, in attach order."""
        user_id = require_user(request, auth_provider)
        await require_owned_task(task_id, user_id)
        found = await asyncio.to_thread(workers.list_for_tasks, [task_id], owner_user_id=user_id)
        return AgentTaskWorkerList(data=[worker_response(worker) for worker in found])

    return router


def adopt_worker(
    board: BoardStores,
    *,
    owner_user_id: str | None,
    task: Task,
    session_id: str,
    title: str | None,
) -> tuple[Worker, bool]:
    """
    Attach a session to a task as its worker, or retitle the worker it has. Blocking.

    The task's manager then follows the session, so it hears when a turn ends.

    :param board: Holds the workers, the managers and their subscriptions.
    :param owner_user_id: The task's owner, or ``None`` in single-user mode.
    :param task: The task.
    :param session_id: The session.
    :param title: The worker's title; ``None`` keeps the current one.
    :returns: The worker, and whether this call created it.
    """
    worker, created = _attach_worker(
        board.workers,
        owner_user_id=owner_user_id,
        task_id=task.id,
        session_id=session_id,
        title=title,
    )
    follow_workers(board, task, owner_user_id=owner_user_id, session_ids=[session_id])
    return worker, created


def _attach_worker(
    workers: WorkerStore,
    *,
    owner_user_id: str | None,
    task_id: str,
    session_id: str,
    title: str | None,
) -> tuple[Worker, bool]:
    existing = workers.get_for_task_and_session(task_id, session_id, owner_user_id=owner_user_id)
    if existing is None:
        try:
            created = workers.create(
                uuid.uuid4().hex,
                owner_user_id=owner_user_id,
                task_id=task_id,
                session_id=session_id,
                title=title,
            )
            return created, True
        except IntegrityError:
            # A concurrent adoption attached the session first.
            existing = workers.get_for_task_and_session(
                task_id, session_id, owner_user_id=owner_user_id
            )
            if existing is None:
                raise
    if title is None or title == existing.title:
        return existing, False
    retitled = workers.update(existing.id, owner_user_id=owner_user_id, title=title)
    return retitled or existing, False


def worker_response(worker: Worker) -> AgentTaskWorkerResponse:
    return AgentTaskWorkerResponse(
        id=worker.id,
        task_id=worker.task_id,
        session_id=worker.session_id,
        title=worker.title,
        created_at=worker.created_at,
        updated_at=worker.updated_at,
    )
