"""The roles that run the caller's GlobalHub board, and the work waiting for them.

The broker waits on unrouted events and on queued requests for managers, each
manager on the events routed to it, and the custodian on queued notice clicks.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.role_sessions import (
    RoleSessionStores,
    current_role_session,
    ensure_role_session,
)
from omnigent.agent_tasks.roles import (
    BROKER_ROLE_KEY,
    CUSTODIAN_ROLE_KEY,
    MANAGER_ROLE_KEY,
    BuiltinRole,
    builtin_role,
)
from omnigent.entities import LIVE_TASK_STATES, Conversation, Manager, TaskEvent
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.agent_task_delivery import SessionSendDeps
from omnigent.server.agent_task_session_states import read_session_states
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    AgentTaskBoardRole,
    AgentTaskBoardRoles,
    AgentTaskRoleWaitingDismissed,
    AgentTaskSpawnManagersRequested,
)


def create_agent_task_role_board_router(
    board: BoardStores,
    sessions: RoleSessionStores,
    session_deps: SessionSendDeps,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the roles that run the caller's board.

    :param board: The board's tasks, managers, events and queue.
    :param sessions: Where the role sessions and the GlobalHub placement live.
    :param session_deps: Reads runner liveness, so a role's turn whose runner is
        gone settles before its row shows it.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    broker, custodian, manager_role = (
        _role(key) for key in (BROKER_ROLE_KEY, CUSTODIAN_ROLE_KEY, MANAGER_ROLE_KEY)
    )

    def waiting_events(user_id: str | None, manager: Manager | None) -> list[TaskEvent]:
        if manager is None:
            return board.events.list(owner_user_id=user_id, states=["unrouted"])
        return board.events.list(owner_user_id=user_id, states=["routed"], manager_id=manager.id)

    def queued_items(user_id: str | None, session: Conversation | None, *kinds: str) -> list[str]:
        if session is None:
            return []
        items = board.queue.list(
            owner_user_id=user_id, states=["queued"], target_session_id=session.id
        )
        return [item.id for item in items if item.kind in kinds]

    def custodian_waiting(
        user_id: str | None, custodian_session: Conversation | None
    ) -> list[str]:
        return queued_items(user_id, custodian_session, "notice_click", "custodian_sweep")

    def broker_waiting(user_id: str | None, broker_session: Conversation | None) -> int:
        requests = queued_items(user_id, broker_session, "spawn_managers")
        return len(waiting_events(user_id, None)) + len(requests)

    @router.get("/agent-tasks/board/roles", response_model=AgentTaskBoardRoles)
    async def read_roles(request: Request) -> AgentTaskBoardRoles:
        """List the roles that run the caller's board, each with its session and waiting work.

        The broker and the custodian come first, then the managers in creation
        order. A manager whose session is gone or archived has no session.
        """
        user_id = require_user(request, auth_provider)

        def load() -> list[tuple[BuiltinRole, Manager | None, Conversation | None, int]]:
            broker_session = current_role_session(broker, user_id=user_id, stores=sessions)
            custodian_session = current_role_session(custodian, user_id=user_id, stores=sessions)
            rows: list[tuple[BuiltinRole, Manager | None, Conversation | None, int]] = [
                (broker, None, broker_session, broker_waiting(user_id, broker_session)),
                (
                    custodian,
                    None,
                    custodian_session,
                    len(custodian_waiting(user_id, custodian_session)),
                ),
            ]
            managers = board.managers.list(owner_user_id=user_id)
            found = sessions.conversation_store.get_conversations(
                [manager.conversation_id for manager in managers if manager.conversation_id]
            )
            for manager in managers:
                session = found.get(manager.conversation_id) if manager.conversation_id else None
                live = session if session is not None and not session.archived else None
                rows.append((manager_role, manager, live, len(waiting_events(user_id, manager))))
            return rows

        rows = await asyncio.to_thread(load)
        states = await read_session_states(
            [session for _role, _manager, session, _waiting in rows if session is not None],
            user_id=user_id,
            conversation_store=sessions.conversation_store,
            deps=session_deps,
        )
        return AgentTaskBoardRoles(
            data=[
                AgentTaskBoardRole(
                    role=role.key,
                    title=manager.title if manager else role.title,
                    manager_id=manager.id if manager else None,
                    session_id=session.id if session else None,
                    session=states.get(session.id) if session else None,
                    waiting=waiting,
                )
                for role, manager, session, waiting in rows
            ]
        )

    @router.post(
        "/agent-tasks/roles/{role}/dismiss-waiting", response_model=AgentTaskRoleWaitingDismissed
    )
    async def dismiss_role_waiting(request: Request, role: str) -> AgentTaskRoleWaitingDismissed:
        """Clear the waiting work of the caller's broker or custodian.

        For the broker, it dismisses the unrouted events and drops the queued
        requests for managers; for the custodian, it drops the queued clicks
        and the queued daily cleanup. A message the role is already handling
        goes on.
        """
        user_id = require_user(request, auth_provider)
        if role not in (BROKER_ROLE_KEY, CUSTODIAN_ROLE_KEY):
            raise OmnigentError(
                f"{role!r} is not the broker or the custodian", code=ErrorCode.NOT_FOUND
            )

        def dismiss() -> int:
            if role == BROKER_ROLE_KEY:
                session = current_role_session(broker, user_id=user_id, stores=sessions)
                events = _dismiss_events(board, waiting_events(user_id, None), user_id)
                requests = queued_items(user_id, session, "spawn_managers")
                return events + _drop_items(board, requests, user_id)
            session = current_role_session(custodian, user_id=user_id, stores=sessions)
            return _drop_items(board, custodian_waiting(user_id, session), user_id)

        dismissed = await asyncio.to_thread(dismiss)
        return AgentTaskRoleWaitingDismissed(role=role, manager_id=None, dismissed=dismissed)

    @router.post(
        "/agent-tasks/managers/{manager_id}/dismiss-waiting",
        response_model=AgentTaskRoleWaitingDismissed,
    )
    async def dismiss_manager_waiting(
        request: Request, manager_id: str
    ) -> AgentTaskRoleWaitingDismissed:
        """Dismiss the events routed to one of the caller's managers.

        A notice the manager is already handling goes on.
        """
        user_id = require_user(request, auth_provider)

        def dismiss() -> int | None:
            manager = board.managers.get(manager_id, owner_user_id=user_id)
            if manager is None:
                return None
            return _dismiss_events(board, waiting_events(user_id, manager), user_id)

        dismissed = await asyncio.to_thread(dismiss)
        if dismissed is None:
            raise OmnigentError("manager not found", code=ErrorCode.NOT_FOUND)
        return AgentTaskRoleWaitingDismissed(
            role=MANAGER_ROLE_KEY, manager_id=manager_id, dismissed=dismissed
        )

    @router.post("/agent-tasks/spawn-managers", response_model=AgentTaskSpawnManagersRequested)
    async def request_managers(request: Request) -> AgentTaskSpawnManagersRequested:
        """Ask the caller's broker to give a manager to each live task that has none.

        The request waits in the queue until the broker is between turns. Its
        message lists the tasks that still have no manager at that time.
        """
        user_id = require_user(request, auth_provider)
        unmanaged = await asyncio.to_thread(
            board.tasks.count, owner_user_id=user_id, states=LIVE_TASK_STATES, unmanaged=True
        )
        if not unmanaged:
            return AgentTaskSpawnManagersRequested(unmanaged_count=0, queued=False)
        broker_session, _ = await ensure_role_session(broker, user_id=user_id, stores=sessions)
        await asyncio.to_thread(
            board.queue.enqueue,
            uuid.uuid4().hex,
            owner_user_id=user_id,
            target_session_id=broker_session.id,
            kind="spawn_managers",
        )
        return AgentTaskSpawnManagersRequested(unmanaged_count=unmanaged, queued=True)

    return router


def _role(key: str) -> BuiltinRole:
    role = builtin_role(key)
    assert role is not None
    return role


def _dismiss_events(board: BoardStores, events: list[TaskEvent], user_id: str | None) -> int:
    # A queued notice whose events are all dismissed is not sent.
    for event in events:
        board.events.dismiss(event.id, owner_user_id=user_id)
    return len(events)


def _drop_items(board: BoardStores, item_ids: list[str], user_id: str | None) -> int:
    return sum(board.queue.delete(item_id, owner_user_id=user_id) for item_id in item_ids)
