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
from omnigent.entities import (
    LIVE_TASK_STATES,
    MANAGER_TASK_CAPACITY,
    Conversation,
    Manager,
    Task,
)
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._agent_task_callers import runner_bound_caller
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._session_create_validation import resolve_host_home_workspace
from omnigent.server.routes._sessions.helpers import _announce_session_added
from omnigent.server.schemas import (
    AgentManagerDeletedResponse,
    AgentManagerList,
    AgentManagerResponse,
    AgentManagerTask,
    AgentTaskState,
    CreateAgentManagerRequest,
    UpdateAgentManagerSelfRequest,
)
from omnigent.stores.user_settings_store import GlobalHubPlacement


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
    async def list_managers(request: Request, include_full: bool = False) -> AgentManagerList:
        """List the caller's managers in creation order, each with its live tasks.

        A manager with ``MANAGER_TASK_CAPACITY`` live tasks or more is full, and
        the list leaves it out, so the broker gives new work to other managers.
        ``include_full`` lists every manager, as the board does.
        """
        user_id = require_user(request, auth_provider)

        def load() -> list[tuple[Manager, list[Task], Conversation | None]]:
            portfolios: defaultdict[str, list[Task]] = defaultdict(list)
            for task in board.tasks.list(owner_user_id=user_id, states=LIVE_TASK_STATES):
                if task.manager_id is not None:
                    portfolios[task.manager_id].append(task)
            managers = [
                manager
                for manager in board.managers.list(owner_user_id=user_id)
                if include_full or len(portfolios[manager.id]) < MANAGER_TASK_CAPACITY
            ]
            manager_sessions = sessions.conversation_store.get_conversations(
                [manager.conversation_id for manager in managers if manager.conversation_id]
            )
            return [
                (
                    manager,
                    portfolios[manager.id],
                    manager_sessions.get(manager.conversation_id)
                    if manager.conversation_id
                    else None,
                )
                for manager in managers
            ]

        listed = await asyncio.to_thread(load)
        return AgentManagerList(
            data=[_manager_response(manager, tasks, session) for manager, tasks, session in listed]
        )

    @router.post("/agent-tasks/managers", response_model=AgentManagerResponse)
    async def create_manager(
        request: Request, body: CreateAgentManagerRequest
    ) -> AgentManagerResponse:
        """Create a manager with its own session, on the GlobalHub host unless pinned."""
        user_id = require_user(request, auth_provider)
        title = body.title or manager_role.title
        globalhub = await asyncio.to_thread(
            require_globalhub_placement, sessions.user_settings_store, user_id
        )
        # Pinning to the GlobalHub host is the same as not pinning.
        pin: GlobalHubPlacement | None = None
        if body.host_id is not None and body.host_id != globalhub.host_id:
            home = await resolve_host_home_workspace(
                user_id=user_id,
                host_id=body.host_id,
                host_store=getattr(request.app.state, "host_store", None),
                host_registry=getattr(request.app.state, "host_registry", None),
            )
            pin = GlobalHubPlacement(host_id=body.host_id, workspace=home)

        def create() -> tuple[Manager, Conversation]:
            session = create_role_session(
                manager_role,
                user_id=user_id,
                placement=pin or globalhub,
                title=title,
                stores=sessions,
            )
            manager = board.managers.create(
                uuid.uuid4().hex,
                owner_user_id=user_id,
                title=title,
                description=body.description,
                conversation_id=session.id,
                host_id=pin.host_id if pin else None,
                workspace=pin.workspace if pin else None,
            )
            return manager, session

        manager, session = await asyncio.to_thread(create)
        _announce_session_added(user_id, session.id)
        return _manager_response(manager, [], session)

    @router.patch("/agent-tasks/managers/self", response_model=AgentManagerResponse)
    async def update_own_manager(
        request: Request, body: UpdateAgentManagerSelfRequest
    ) -> AgentManagerResponse:
        """Let a manager's session replace its own manager's description.

        Only the manager's runner can name its session, so the board UI cannot
        call this.
        """
        user_id = require_user(request, auth_provider)

        def update() -> tuple[Manager, list[Task], Conversation]:
            session = runner_bound_caller(request, sessions.conversation_store)
            if session is None:
                raise OmnigentError(
                    "only a manager's own session can update it", code=ErrorCode.FORBIDDEN
                )
            manager = board.managers.get_by_conversation_id(session.id, owner_user_id=user_id)
            if manager is None:
                raise OmnigentError(
                    "the calling session is not one of your managers", code=ErrorCode.FORBIDDEN
                )
            updated = board.managers.update(
                manager.id, owner_user_id=user_id, description=body.description
            )
            tasks = board.tasks.list(
                owner_user_id=user_id, states=LIVE_TASK_STATES, manager_id=manager.id
            )
            return updated or manager, tasks, session

        manager, tasks, session = await asyncio.to_thread(update)
        return _manager_response(manager, tasks, session)

    @router.delete(
        "/agent-tasks/managers/{manager_id}", response_model=AgentManagerDeletedResponse
    )
    async def delete_manager(request: Request, manager_id: str) -> AgentManagerDeletedResponse:
        """Delete one of the caller's managers and unassign its tasks.

        Its unhandled events wait for the broker again. The manager's session is
        left as an ordinary session.
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


def _manager_response(
    manager: Manager, tasks: list[Task], session: Conversation | None
) -> AgentManagerResponse:
    return AgentManagerResponse(
        id=manager.id,
        conversation_id=manager.conversation_id,
        title=manager.title,
        description=manager.description,
        host_id=session.host_id if session else None,
        workspace=session.workspace if session else None,
        session_missing=session is None or session.archived,
        task_count=len(tasks),
        tasks=[
            AgentManagerTask(id=task.id, title=task.title, state=cast(AgentTaskState, task.state))
            for task in tasks
        ],
    )
