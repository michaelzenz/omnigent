"""The caller's GlobalHub board tasks (``/v1/agent-tasks``).

Routes on ``/agent-tasks/{task_id}`` also match single-segment paths such as
``/agent-tasks/managers``; routers serving those must be included first.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import cast

from fastapi import APIRouter, Query, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.worker_subscriptions import follow_workers, unfollow_workers
from omnigent.entities import LIVE_TASK_STATES, Task
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    AgentTaskDeletedResponse,
    AgentTaskList,
    AgentTaskResponse,
    AgentTaskState,
    BatchGetAgentTasksRequest,
    CreateAgentTaskRequest,
    UpdateAgentTaskRequest,
)
from omnigent.stores.task_store import UNSET
from omnigent.util.uuid import InvalidUuidError, uuid_to_bytes


def create_agent_tasks_router(
    stores: BoardStores,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the caller's GlobalHub board tasks.

    :param stores: The board's tasks, managers, workers and subscriptions.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    tasks = stores.tasks
    managers = stores.managers

    async def require_owned_manager(manager_id: str, user_id: str | None) -> None:
        manager = await asyncio.to_thread(managers.get, manager_id, owner_user_id=user_id)
        if manager is None:
            raise OmnigentError("manager not found", code=ErrorCode.NOT_FOUND)

    @router.post("/agent-tasks", response_model=AgentTaskResponse)
    async def create_task(request: Request, body: CreateAgentTaskRequest) -> AgentTaskResponse:
        """Create a task at the top of the caller's board, owned by one of their managers."""
        user_id = require_user(request, auth_provider)
        await require_owned_manager(body.manager_id, user_id)
        task = await asyncio.to_thread(
            tasks.create,
            uuid.uuid4().hex,
            owner_user_id=user_id,
            title=body.title,
            description=body.description,
            internal_note=body.internal_note,
            manager_id=body.manager_id,
            priority=body.priority,
        )
        return _task_response(task)

    @router.get("/agent-tasks", response_model=AgentTaskList)
    async def list_tasks(
        request: Request,
        state: AgentTaskState | None = None,
        manager_id: str | None = None,
        manager_conversation_id: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> AgentTaskList:
        """List the caller's tasks in board order.

        ``manager_conversation_id`` selects the tasks of the manager whose
        session it is, so a manager can list its own tasks.
        """
        user_id = require_user(request, auth_provider)
        if manager_conversation_id is not None:
            manager = (
                await asyncio.to_thread(
                    managers.get_by_conversation_id,
                    manager_conversation_id,
                    owner_user_id=user_id,
                )
                if _is_uuid(manager_conversation_id)
                else None
            )
            conflicting_manager = (
                manager_id is not None and manager is not None and manager_id != manager.id
            )
            if manager is None or conflicting_manager:
                return AgentTaskList(data=[])
            manager_id = manager.id
        if manager_id is not None and not _is_uuid(manager_id):
            return AgentTaskList(data=[])
        found = await asyncio.to_thread(
            tasks.list,
            owner_user_id=user_id,
            states=[state] if state is not None else None,
            manager_id=manager_id,
            limit=limit,
        )
        return AgentTaskList(data=[_task_response(task) for task in found])

    @router.post("/agent-tasks/batch", response_model=AgentTaskList)
    async def batch_get_tasks(request: Request, body: BatchGetAgentTasksRequest) -> AgentTaskList:
        """Fetch several of the caller's tasks by id, in request order; unknown ids are skipped."""
        user_id = require_user(request, auth_provider)

        def fetch() -> list[Task]:
            found: list[Task] = []
            for task_id in dict.fromkeys(task_id.strip() for task_id in body.task_ids):
                task = tasks.get(task_id, owner_user_id=user_id) if _is_uuid(task_id) else None
                if task is not None:
                    found.append(task)
            return found

        return AgentTaskList(
            data=[_task_response(task) for task in await asyncio.to_thread(fetch)]
        )

    @router.get("/agent-tasks/{task_id}", response_model=AgentTaskResponse)
    async def get_task(request: Request, task_id: str) -> AgentTaskResponse:
        """Read one of the caller's tasks."""
        user_id = require_user(request, auth_provider)
        task = await asyncio.to_thread(tasks.get, task_id, owner_user_id=user_id)
        return _task_response(_require_task(task))

    @router.patch("/agent-tasks/{task_id}", response_model=AgentTaskResponse)
    async def update_task(
        request: Request, task_id: str, body: UpdateAgentTaskRequest
    ) -> AgentTaskResponse:
        """Change fields of one of the caller's tasks.

        A change of ``state`` also moves the task on the board: ``agent-resolved``
        to the end, any other state to the top so revived work surfaces. A
        ``state`` equal to the task's current state moves nothing. The manager
        of a live task follows its workers' sessions, so a new manager, an
        archived task or a revived one changes which manager follows them.
        """
        user_id = require_user(request, auth_provider)
        reassigns = "manager_id" in body.model_fields_set
        if reassigns and body.manager_id is not None:
            await require_owned_manager(body.manager_id, user_id)

        def update() -> Task | None:
            before = tasks.get(task_id, owner_user_id=user_id)
            task = tasks.update(
                task_id,
                owner_user_id=user_id,
                title=body.title,
                description=body.description,
                internal_note=body.internal_note,
                manager_id=body.manager_id if reassigns else UNSET,
                state=body.state,
                priority=body.priority,
            )
            if task is not None and before is not None:
                _move_worker_subscriptions(stores, before, task, user_id)
            if task is None or before is None or task.state == before.state:
                return task
            if task.state == "agent-resolved":
                return tasks.move_to_board_end(task_id, owner_user_id=user_id)
            return tasks.move_to_board_top(task_id, owner_user_id=user_id)

        return _task_response(_require_task(await asyncio.to_thread(update)))

    @router.post("/agent-tasks/{task_id}/move-to-queue-end", response_model=AgentTaskResponse)
    async def move_task_to_board_end(request: Request, task_id: str) -> AgentTaskResponse:
        """Move one of the caller's tasks toward the end of their board.

        A live task goes just above the first ``agent-resolved`` task; an
        ``agent-resolved`` task goes to the very end.
        """
        user_id = require_user(request, auth_provider)
        task = await asyncio.to_thread(tasks.move_to_board_end, task_id, owner_user_id=user_id)
        return _task_response(_require_task(task))

    @router.delete("/agent-tasks/{task_id}/permanent", response_model=AgentTaskDeletedResponse)
    async def delete_task_permanently(request: Request, task_id: str) -> AgentTaskDeletedResponse:
        """Permanently delete one of the caller's tasks, its workers and its assets.

        The manager stops following the workers' sessions, unless they work on
        another of its live tasks. The manager's and workers' sessions keep
        running as ordinary sessions.
        """
        user_id = require_user(request, auth_provider)

        def delete() -> bool:
            task = tasks.get(task_id, owner_user_id=user_id)
            if task is None:
                return False
            unfollow_workers(stores, task, owner_user_id=user_id, manager_id=task.manager_id)
            return tasks.delete(task_id, owner_user_id=user_id)

        if not await asyncio.to_thread(delete):
            raise OmnigentError("task not found", code=ErrorCode.NOT_FOUND)
        return AgentTaskDeletedResponse(id=task_id)

    return router


def _move_worker_subscriptions(
    board: BoardStores, before: Task, after: Task, owner_user_id: str | None
) -> None:
    """Make the manager of a live task follow its workers after a change of manager or state."""
    was_live = before.state in LIVE_TASK_STATES
    is_live = after.state in LIVE_TASK_STATES
    if before.manager_id == after.manager_id and was_live == is_live:
        return
    if was_live:
        unfollow_workers(board, before, owner_user_id=owner_user_id, manager_id=before.manager_id)
    if is_live:
        follow_workers(board, after, owner_user_id=owner_user_id)


def _is_uuid(value: str) -> bool:
    try:
        uuid_to_bytes(value)
    except InvalidUuidError:
        return False
    return True


def _require_task(task: Task | None) -> Task:
    if task is None:
        raise OmnigentError("task not found", code=ErrorCode.NOT_FOUND)
    return task


def _task_response(task: Task) -> AgentTaskResponse:
    return AgentTaskResponse(
        id=task.id,
        manager_id=task.manager_id,
        owner_user_id=task.owner_user_id,
        title=task.title,
        description=task.description,
        internal_note=task.internal_note,
        state=cast(AgentTaskState, task.state),
        priority=task.priority,
        queue_rank=task.queue_rank,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
