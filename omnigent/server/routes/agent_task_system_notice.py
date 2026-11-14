"""The system notice pinned on the caller's GlobalHub board (``/v1/agent-tasks/system-notice``).

Include this router before the tasks router: ``/agent-tasks/{task_id}`` would
otherwise match ``/agent-tasks/system-notice``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from typing import Any, cast

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_role_session
from omnigent.agent_tasks.roles import CUSTODIAN_ROLE_KEY, builtin_role
from omnigent.entities import SystemNotice
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._agent_task_callers import runner_bound_caller
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    AgentSystemNoticeAction,
    AgentSystemNoticeBodyKind,
    AgentSystemNoticeClickResponse,
    AgentSystemNoticeResponse,
    AgentSystemNoticeState,
    AgentTaskBoardSystemNotice,
    ClickAgentSystemNoticeActionRequest,
    PublishAgentSystemNoticeRequest,
)
from omnigent.stores import ConversationStore

# The width of the publisher_label column.
_PUBLISHER_LABEL_MAX_CHARS = 256
_TEXT_INPUT_MAX_CHARS = 8_000
_CLICK_VALUES_MAX_BYTES = 2 * 1024 * 1024


def create_agent_task_system_notice_router(
    board: BoardStores,
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
    sessions: RoleSessionStores | None = None,
) -> APIRouter:
    """Build the router for the system notice on the caller's GlobalHub board.

    :param board: The board's system notices and the delivery queue.
    :param conversation_store: Finds the session that publishes a notice.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :param sessions: Where each user's custodian session lives; without it a
        click is refused.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    notices = board.system_notices
    custodian_role = builtin_role(CUSTODIAN_ROLE_KEY)
    assert custodian_role is not None

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

    @router.post(
        "/agent-tasks/system-notice/actions/{action_id}/click",
        response_model=AgentSystemNoticeClickResponse,
    )
    async def click_system_notice_action(
        request: Request, action_id: str, body: ClickAgentSystemNoticeActionRequest
    ) -> AgentSystemNoticeClickResponse:
        """Send a click on an action of the caller's active notice to their custodian.

        The click waits in the queue until the custodian session is between
        turns. The notice stays active; the custodian clears it after acting.
        """
        user_id = require_user(request, auth_provider)
        if sessions is None:
            raise OmnigentError(
                "GlobalHub roles are not available on this server", code=ErrorCode.INVALID_INPUT
            )
        notice = await asyncio.to_thread(notices.get_active, owner_user_id=user_id)
        if notice is None:
            raise OmnigentError("no system notice is active", code=ErrorCode.NOT_FOUND)
        action = next(
            (
                AgentSystemNoticeAction.model_validate(declared)
                for declared in notice.actions
                if declared.get("id") == action_id
            ),
            None,
        )
        if action is None:
            raise OmnigentError(
                f"the notice declares no action {action_id!r}", code=ErrorCode.NOT_FOUND
            )
        values = _click_values(action, body.inputs)
        custodian, _ = await ensure_role_session(custodian_role, user_id=user_id, stores=sessions)
        await asyncio.to_thread(
            board.queue.enqueue,
            uuid.uuid4().hex,
            owner_user_id=user_id,
            target_session_id=custodian.id,
            kind="notice_click",
            payload={
                "notice_id": notice.id,
                "title": notice.title,
                "action_id": action.id,
                "action_label": action.label,
                "publisher_label": notice.publisher_label,
                "inputs": values,
            },
        )
        return AgentSystemNoticeClickResponse(
            notice_id=notice.id, action_id=action.id, inputs=values
        )

    return router


def _click_values(
    action: AgentSystemNoticeAction, inputs: Mapping[str, Any]
) -> dict[str, bool | str]:
    """Check a click's values against the action's declared inputs and return them."""
    declared_ids = {declared.id for declared in action.inputs}
    unknown = next((input_id for input_id in inputs if input_id not in declared_ids), None)
    if unknown is not None:
        raise OmnigentError(
            f"action {action.id!r} declares no input {unknown!r}", code=ErrorCode.INVALID_INPUT
        )
    values: dict[str, bool | str] = {}
    for declared in action.inputs:
        value = inputs.get(declared.id)
        if declared.kind == "toggle":
            if value is not None and not isinstance(value, bool):
                raise OmnigentError(
                    f"input {declared.id!r} must be true or false", code=ErrorCode.INVALID_INPUT
                )
        elif value is not None and (
            not isinstance(value, str) or len(value) > _TEXT_INPUT_MAX_CHARS
        ):
            raise OmnigentError(
                f"input {declared.id!r} must be text of at most {_TEXT_INPUT_MAX_CHARS} "
                "characters",
                code=ErrorCode.INVALID_INPUT,
            )
        elif declared.required and not (value or "").strip():
            raise OmnigentError(f"input {declared.id!r} is required", code=ErrorCode.INVALID_INPUT)
        if value is not None:
            values[declared.id] = value
    if len(json.dumps(values, ensure_ascii=False).encode()) > _CLICK_VALUES_MAX_BYTES:
        raise OmnigentError(
            "the input values must serialize to at most 2 MB of JSON",
            code=ErrorCode.INVALID_INPUT,
        )
    return values


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
