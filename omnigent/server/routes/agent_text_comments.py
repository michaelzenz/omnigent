"""Routes for unsent comments anchored to finalized agent responses."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, model_validator

from omnigent.entities import MessageData
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_access
from omnigent.server.routes._errors import session_not_found
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore


class AddAgentTextCommentRequest(BaseModel):
    conversation_item_id: str
    start_offset: int
    end_offset: int
    selected_text: str
    prefix_context: str = ""
    suffix_context: str = ""
    body: str

    @model_validator(mode="after")
    def _validate_anchor(self) -> AddAgentTextCommentRequest:
        if self.start_offset < 0:
            raise ValueError("start_offset must be >= 0")
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be > start_offset")
        if not self.selected_text.strip():
            raise ValueError("selected_text must not be empty")
        if not self.body.strip():
            raise ValueError("body must not be empty")
        return self


class UpdateAgentTextCommentRequest(BaseModel):
    body: str

    @model_validator(mode="after")
    def _validate_body(self) -> UpdateAgentTextCommentRequest:
        if not self.body.strip():
            raise ValueError("body must not be empty")
        return self


class DeleteAgentTextCommentsRequest(BaseModel):
    comment_ids: list[str]


def create_agent_text_comments_router(
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build CRUD routes for the current unsent agent-response review batch."""
    router = APIRouter()

    async def require_session_access(
        user_id: str | None,
        session_id: str,
        level: int,
    ) -> None:
        if permission_store is not None:
            await require_access(
                user_id,
                session_id,
                level,
                permission_store,
                conversation_store,
            )
        conversation = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conversation is None:
            raise session_not_found()

    @router.get("/sessions/{session_id}/agent-text-comments")
    async def list_agent_text_comments(
        request: Request,
        session_id: str,
    ) -> list[dict[str, Any]]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_READ)
        comments = await asyncio.to_thread(
            conversation_store.list_agent_text_comments,
            session_id,
        )
        return [asdict(comment) for comment in comments]

    @router.post("/sessions/{session_id}/agent-text-comments")
    async def add_agent_text_comment(
        request: Request,
        session_id: str,
        body: AddAgentTextCommentRequest,
    ) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        item = await asyncio.to_thread(
            conversation_store.get_item,
            session_id,
            body.conversation_item_id,
        )
        if item is None:
            raise OmnigentError("Agent text item not found", code=ErrorCode.NOT_FOUND)
        if (
            item.type != "message"
            or not isinstance(item.data, MessageData)
            or item.data.role != "assistant"
            or item.status != "completed"
            or item.data.is_meta
        ):
            raise OmnigentError(
                "Comments can only target completed agent text",
                code=ErrorCode.INVALID_INPUT,
            )
        selected_text_utf16_length = len(body.selected_text.encode("utf-16-le")) // 2
        if body.end_offset - body.start_offset != selected_text_utf16_length:
            raise OmnigentError(
                "Selected text length does not match its offsets",
                code=ErrorCode.INVALID_INPUT,
            )
        try:
            comment = await asyncio.to_thread(
                conversation_store.add_agent_text_comment,
                session_id,
                body.conversation_item_id,
                start_offset=body.start_offset,
                end_offset=body.end_offset,
                selected_text=body.selected_text,
                prefix_context=body.prefix_context,
                suffix_context=body.suffix_context,
                body=body.body.strip(),
            )
        except LookupError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.NOT_FOUND) from exc
        return asdict(comment)

    @router.patch("/sessions/{session_id}/agent-text-comments/{comment_id}")
    async def update_agent_text_comment(
        request: Request,
        session_id: str,
        comment_id: str,
        body: UpdateAgentTextCommentRequest,
    ) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        comment = await asyncio.to_thread(
            conversation_store.update_agent_text_comment,
            comment_id,
            session_id,
            body=body.body.strip(),
        )
        if comment is None:
            raise OmnigentError("Agent text comment not found", code=ErrorCode.NOT_FOUND)
        return asdict(comment)

    @router.delete("/sessions/{session_id}/agent-text-comments/{comment_id}")
    async def delete_agent_text_comment(
        request: Request,
        session_id: str,
        comment_id: str,
    ) -> dict[str, bool]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        deleted = await asyncio.to_thread(
            conversation_store.delete_agent_text_comment,
            comment_id,
            session_id,
        )
        if deleted is None:
            raise OmnigentError("Agent text comment not found", code=ErrorCode.NOT_FOUND)
        return {"deleted": True}

    @router.post("/sessions/{session_id}/agent-text-comments/delete-batch")
    async def delete_agent_text_comments(
        request: Request,
        session_id: str,
        body: DeleteAgentTextCommentsRequest,
    ) -> dict[str, list[str]]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        deleted = await asyncio.to_thread(
            conversation_store.delete_agent_text_comments,
            body.comment_ids,
            session_id,
        )
        return {"deleted_comment_ids": deleted}

    return router
