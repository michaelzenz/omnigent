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
from omnigent.agent_tasks.notice_changes import (
    MAX_NOTICE_CHANGES,
    check_changes,
    compose_notice,
    stage_change,
)
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_role_session
from omnigent.agent_tasks.roles import CUSTODIAN_ROLE_KEY, builtin_role, session_role
from omnigent.entities import SystemNotice
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.identity import ORCHESTRATION_SOURCE_HEADER, RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._agent_task_callers import runner_bound_caller
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    AgentSystemNoticeAction,
    AgentSystemNoticeBodyKind,
    AgentSystemNoticeClickResponse,
    AgentSystemNoticeDraft,
    AgentSystemNoticeResponse,
    AgentSystemNoticeState,
    AgentTaskBoardSystemNotice,
    ClickAgentSystemNoticeActionRequest,
    CommitAgentSystemNoticeDraftRequest,
    PublishAgentSystemNoticeRequest,
    StageAgentNoticeChangeRequest,
)
from omnigent.stores import ConversationStore
from omnigent.stores.user_settings_store import UserSettingsStore, open_user_settings_store

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

    def require_custodian_or_user(request: Request) -> None:
        """Refuse a change to the notice or its draft from agents but the custodian. Blocking."""
        if not _from_agent(request):
            return
        caller = runner_bound_caller(request, conversation_store)
        role = session_role(caller.labels) if caller is not None else None
        if role is None or role.key != CUSTODIAN_ROLE_KEY:
            raise OmnigentError(
                "only the custodian or the user can change the system notice",
                code=ErrorCode.FORBIDDEN,
            )

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
            require_custodian_or_user(request)
            publisher_id, publisher_label = _publisher(request, conversation_store)
            return notices.publish(
                uuid.uuid4().hex,
                owner_user_id=user_id,
                title=body.title,
                body_kind=body.body_kind,
                body=body.body,
                actions=body.declared_actions(),
                publisher_session_id=publisher_id,
                publisher_label=publisher_label,
            )

        return _board_notice(await asyncio.to_thread(publish))

    def draft_view(user_id: str | None, changes: list[dict[str, Any]]) -> AgentSystemNoticeDraft:
        problems = check_changes(
            changes, owner_user_id=user_id, board=board, conversation_store=conversation_store
        )
        return AgentSystemNoticeDraft(changes=changes, problems=problems)

    @router.get("/agent-tasks/system-notice/draft", response_model=AgentSystemNoticeDraft)
    async def get_notice_draft(request: Request) -> AgentSystemNoticeDraft:
        """Return the changes staged for the caller's next notice, and the check's problems."""
        user_id = require_user(request, auth_provider)

        def read() -> AgentSystemNoticeDraft:
            changes = _settings(conversation_store).get_globalhub_notice_draft(user_id=user_id)
            return draft_view(user_id, changes)

        return await asyncio.to_thread(read)

    @router.post("/agent-tasks/system-notice/draft/changes", response_model=AgentSystemNoticeDraft)
    async def stage_notice_change(
        request: Request, body: StageAgentNoticeChangeRequest
    ) -> AgentSystemNoticeDraft:
        """Stage one change for the caller's next notice; nobody sees it until a commit.

        A change for the same kind and subject replaces the staged one. The
        draft holds at most as many changes as one notice.
        """
        user_id = require_user(request, auth_provider)
        change = body.change.model_dump(exclude_none=True)

        def add(staged: list[dict[str, Any]]) -> list[dict[str, Any]]:
            draft = stage_change(staged, change)
            if len(draft) > MAX_NOTICE_CHANGES:
                raise OmnigentError(
                    f"a notice holds at most {MAX_NOTICE_CHANGES} changes; commit or unstage "
                    "a change first",
                    code=ErrorCode.INVALID_INPUT,
                )
            return draft

        def stage() -> AgentSystemNoticeDraft:
            require_custodian_or_user(request)
            draft = _settings(conversation_store).change_globalhub_notice_draft(
                add, user_id=user_id
            )
            return draft_view(user_id, draft)

        return await asyncio.to_thread(stage)

    @router.delete(
        "/agent-tasks/system-notice/draft/changes/{change_id}",
        response_model=AgentSystemNoticeDraft,
    )
    async def unstage_notice_change(request: Request, change_id: str) -> AgentSystemNoticeDraft:
        """Remove one staged change from the caller's draft."""
        user_id = require_user(request, auth_provider)

        def remove(staged: list[dict[str, Any]]) -> list[dict[str, Any]]:
            draft = [change for change in staged if change.get("id") != change_id]
            if len(draft) == len(staged):
                raise OmnigentError("no staged change has that id", code=ErrorCode.NOT_FOUND)
            return draft

        def unstage() -> AgentSystemNoticeDraft:
            require_custodian_or_user(request)
            draft = _settings(conversation_store).change_globalhub_notice_draft(
                remove, user_id=user_id
            )
            return draft_view(user_id, draft)

        return await asyncio.to_thread(unstage)

    @router.delete("/agent-tasks/system-notice/draft", response_model=AgentSystemNoticeDraft)
    async def discard_notice_draft(request: Request) -> AgentSystemNoticeDraft:
        """Remove every staged change from the caller's draft."""
        user_id = require_user(request, auth_provider)

        def discard() -> AgentSystemNoticeDraft:
            require_custodian_or_user(request)
            _settings(conversation_store).save_globalhub_notice_draft([], user_id=user_id)
            return AgentSystemNoticeDraft(changes=[], problems=[])

        return await asyncio.to_thread(discard)

    @router.post(
        "/agent-tasks/system-notice/draft/commit", response_model=AgentTaskBoardSystemNotice
    )
    async def commit_notice_draft(
        request: Request, body: CommitAgentSystemNoticeDraftRequest | None = None
    ) -> AgentTaskBoardSystemNotice:
        """Publish the staged changes as the caller's notice, which replaces the active one.

        The commit runs the final check and publishes nothing while it finds a
        problem. The notice lists the changes with one toggle each, and the
        commit takes the published changes out of the draft.
        """
        user_id = require_user(request, auth_provider)

        def commit() -> SystemNotice:
            require_custodian_or_user(request)
            settings = _settings(conversation_store)
            staged = settings.get_globalhub_notice_draft(user_id=user_id)
            if not staged:
                raise OmnigentError("no change is staged", code=ErrorCode.INVALID_INPUT)
            problems = draft_view(user_id, staged).problems
            if problems:
                raise OmnigentError(
                    "the final check found problems: " + " ".join(problems),
                    code=ErrorCode.INVALID_INPUT,
                )
            title, notice_body, actions, ordered = compose_notice(
                staged,
                owner_user_id=user_id,
                board=board,
                conversation_store=conversation_store,
                title=body.title if body else None,
            )
            publisher_id, publisher_label = _publisher(request, conversation_store)
            notice = notices.publish(
                uuid.uuid4().hex,
                owner_user_id=user_id,
                title=title,
                body=notice_body,
                actions=actions,
                changes=ordered,
                publisher_session_id=publisher_id,
                publisher_label=publisher_label,
            )
            settings.change_globalhub_notice_draft(
                lambda current: [change for change in current if change not in staged],
                user_id=user_id,
            )
            return notice

        return _board_notice(await asyncio.to_thread(commit))

    @router.post("/agent-tasks/system-notice/dismiss", response_model=AgentTaskBoardSystemNotice)
    async def dismiss_system_notice(request: Request) -> AgentTaskBoardSystemNotice:
        """Resolve the caller's active system notice and return it; null when there is none.

        Dismissal only resolves the notice; no session hears of it.
        """
        user_id = require_user(request, auth_provider)

        def dismiss() -> SystemNotice | None:
            require_custodian_or_user(request)
            return notices.resolve_active(owner_user_id=user_id)

        return _board_notice(await asyncio.to_thread(dismiss))

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
        if _from_agent(request):
            raise OmnigentError("only the user can click a notice", code=ErrorCode.FORBIDDEN)
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
        payload: dict[str, Any] = {
            "notice_id": notice.id,
            "title": notice.title,
            "action_id": action.id,
            "action_label": action.label,
            "publisher_label": notice.publisher_label,
            "inputs": values,
        }
        if notice.changes:
            approved = {input_id for input_id, value in values.items() if value is True}
            payload["changes"] = [
                {field: value for field, value in change.items() if field != "details"}
                for change in notice.changes
                if change.get("id") in approved
            ]
        await asyncio.to_thread(
            board.queue.enqueue,
            uuid.uuid4().hex,
            owner_user_id=user_id,
            target_session_id=custodian.id,
            kind="notice_click",
            payload=payload,
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
            changes=notice.changes,
        )
    )


def _publisher(
    request: Request, conversation_store: ConversationStore
) -> tuple[str | None, str | None]:
    """Return the id and label of the session whose runner sent the request. Blocking."""
    publisher = runner_bound_caller(request, conversation_store)
    if publisher is None:
        return None, None
    label = publisher.title[:_PUBLISHER_LABEL_MAX_CHARS] if publisher.title else None
    return publisher.id, label


def _from_agent(request: Request) -> bool:
    """Whether a runner sent the request for an agent's session, not the user's browser."""
    return bool(
        request.headers.get(ORCHESTRATION_SOURCE_HEADER)
        or request.headers.get(RUNNER_TUNNEL_TOKEN_HEADER)
    )


def _settings(conversation_store: ConversationStore) -> UserSettingsStore:
    settings = open_user_settings_store(conversation_store)
    if settings is None:
        raise OmnigentError(
            "Staging a notice needs a SQL database on this server", code=ErrorCode.INVALID_INPUT
        )
    return settings
