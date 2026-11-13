"""The caller's GlobalHub managers (``/v1/agent-tasks/managers``).

Include this router before the tasks router: ``/agent-tasks/{task_id}`` would
otherwise match ``/agent-tasks/managers``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import cast

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.role_sessions import (
    RoleSessionStores,
    create_role_session,
    require_globalhub_placement,
)
from omnigent.agent_tasks.roles import MANAGER_ROLE_KEY, builtin_role
from omnigent.entities import Manager, Task
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._sessions.helpers import _announce_session_added
from omnigent.server.schemas import (
    AgentManagerDeletedResponse,
    AgentManagerList,
    AgentManagerResponse,
    AgentManagerTask,
    AgentTaskState,
    CreateAgentManagerRequest,
)

# Archived tasks no longer count toward a manager's portfolio.
_LIVE_TASK_STATES = ("active", "idle", "agent-resolved")


def create_agent_task_managers_router(
    board: BoardStores,
    sessions: RoleSessionStores,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the caller's GlobalHub managers.

    :param board: The board's tasks and managers.
    :param sessions: Where manager sessions are created.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    manager_role = builtin_role(MANAGER_ROLE_KEY)
    assert manager_role is not None

    @router.get("/agent-tasks/managers", response_model=AgentManagerList)
    async def list_managers(request: Request) -> AgentManagerList:
        """List the caller's managers in creation order, each with its live tasks."""
        user_id = require_user(request, auth_provider)

        def load() -> list[tuple[Manager, list[Task]]]:
            managers = board.managers.list(owner_user_id=user_id)
            portfolios: defaultdict[str, list[Task]] = defaultdict(list)
            for task in board.tasks.list(owner_user_id=user_id, states=_LIVE_TASK_STATES):
                if task.manager_id is not None:
                    portfolios[task.manager_id].append(task)
            return [(manager, portfolios[manager.id]) for manager in managers]

        listed = await asyncio.to_thread(load)
        return AgentManagerList(data=[_manager_response(m, tasks) for m, tasks in listed])

    @router.post("/agent-tasks/managers", response_model=AgentManagerResponse)
    async def create_manager(
        request: Request, body: CreateAgentManagerRequest
    ) -> AgentManagerResponse:
        """Create a manager with its own session on the caller's GlobalHub host."""
        user_id = require_user(request, auth_provider)
        title = body.title or manager_role.title

        def create() -> Manager:
            placement = require_globalhub_placement(sessions.user_settings_store, user_id)
            session = create_role_session(
                manager_role, user_id=user_id, placement=placement, title=title, stores=sessions
            )
            return board.managers.create(
                uuid.uuid4().hex,
                owner_user_id=user_id,
                title=title,
                description=body.description,
                conversation_id=session.id,
            )

        manager = await asyncio.to_thread(create)
        if manager.conversation_id is not None:
            _announce_session_added(user_id, manager.conversation_id)
        return _manager_response(manager, [])

    @router.delete(
        "/agent-tasks/managers/{manager_id}", response_model=AgentManagerDeletedResponse
    )
    async def delete_manager(request: Request, manager_id: str) -> AgentManagerDeletedResponse:
        """Delete one of the caller's managers and unassign its tasks.

        The manager's session is left as an ordinary session.
        """
        user_id = require_user(request, auth_provider)

        def delete() -> Manager | None:
            manager = board.managers.get(manager_id, owner_user_id=user_id)
            if manager is None or not board.managers.delete(manager_id, owner_user_id=user_id):
                return None
            return manager

        manager = await asyncio.to_thread(delete)
        if manager is None:
            raise OmnigentError("manager not found", code=ErrorCode.NOT_FOUND)
        return AgentManagerDeletedResponse(id=manager.id, conversation_id=manager.conversation_id)

    return router


def _manager_response(manager: Manager, tasks: list[Task]) -> AgentManagerResponse:
    return AgentManagerResponse(
        id=manager.id,
        conversation_id=manager.conversation_id,
        title=manager.title,
        description=manager.description,
        host_id=manager.host_id,
        workspace=manager.workspace,
        task_count=len(tasks),
        tasks=[
            AgentManagerTask(id=task.id, title=task.title, state=cast(AgentTaskState, task.state))
            for task in tasks
        ],
    )
