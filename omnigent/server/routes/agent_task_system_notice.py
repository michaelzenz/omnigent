"""The system notice pinned on the caller's GlobalHub board (``/v1/agent-tasks/system-notice``).

Include this router before the tasks router: ``/agent-tasks/{task_id}`` would
otherwise match ``/agent-tasks/system-notice``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import cast

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import SystemNotice
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._agent_task_callers import runner_bound_caller
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    AgentSystemNoticeAction,
    AgentSystemNoticeBodyKind,
    AgentSystemNoticeResponse,
    AgentSystemNoticeState,
    AgentTaskBoardSystemNotice,
    PublishAgentSystemNoticeRequest,
)
from omnigent.stores import ConversationStore

# The width of the publisher_label column.
_PUBLISHER_LABEL_MAX_CHARS = 256


def create_agent_task_system_notice_router(
    board: BoardStores,
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the system notice on the caller's GlobalHub board.

    :param board: The board's system notices.
    :param conversation_store: Finds the session that publishes a notice.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    notices = board.system_notices

    @router.get("/agent-tasks/system-notice", response_model=AgentTaskBoardSystemNotice)
    async def get_system_notice(request: Request) -> AgentTaskBoardSystemNotice:
        """Return the caller's active system notice, or null when there is none."""
        user_id = require_user(request, auth_provider)
        notice = await asyncio.to_thread(notices.get_active, owner_user_id=user_id)
        return _board_notice(notice)

    @router.post("/agent-tasks/system-notice", response_model=AgentTaskBoardSystemNotice)
    async def publish_system_notice(
        request: Request, body: PublishAgentSystemNoticeRequest
    ) -> AgentTaskBoardSystemNotice:
        """Publish the caller's system notice; it replaces the active one.

        The notice names its publisher only when a session's runner sends the
        request for that session.
        """
        user_id = require_user(request, auth_provider)

        def publish() -> SystemNotice:
            publisher = runner_bound_caller(request, conversation_store)
            return notices.publish(
                uuid.uuid4().hex,
                owner_user_id=user_id,
                title=body.title,
                body_kind=body.body_kind,
                body=body.body,
                actions=body.declared_actions(),
                publisher_session_id=publisher.id if publisher else None,
                publisher_label=(
                    publisher.title[:_PUBLISHER_LABEL_MAX_CHARS]
                    if publisher and publisher.title
                    else None
                ),
            )

        return _board_notice(await asyncio.to_thread(publish))

    @router.post("/agent-tasks/system-notice/dismiss", response_model=AgentTaskBoardSystemNotice)
    async def dismiss_system_notice(request: Request) -> AgentTaskBoardSystemNotice:
        """Resolve the caller's active system notice and return it; null when there is none.

        Dismissal only resolves the notice; no session hears of it.
        """
        user_id = require_user(request, auth_provider)
        notice = await asyncio.to_thread(notices.resolve_active, owner_user_id=user_id)
        return _board_notice(notice)

    return router


def _board_notice(notice: SystemNotice | None) -> AgentTaskBoardSystemNotice:
    if notice is None:
        return AgentTaskBoardSystemNotice(notice=None)
    return AgentTaskBoardSystemNotice(
        notice=AgentSystemNoticeResponse(
            id=notice.id,
            title=notice.title,
            body_kind=cast(AgentSystemNoticeBodyKind, notice.body_kind),
            body=notice.body,
            actions=[AgentSystemNoticeAction.model_validate(action) for action in notice.actions],
            state=cast(AgentSystemNoticeState, notice.state),
            publisher_session_id=notice.publisher_session_id,
            publisher_label=notice.publisher_label,
            created_at=notice.created_at,
            updated_at=notice.updated_at,
            resolved_at=notice.resolved_at,
        )
    )
