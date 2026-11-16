"""The caller's unsent comments on agent replies (``/v1/sessions/{id}/agent-text-comments``).

Each user keeps their own batch of comments in a session: every route reads and
changes only the caller's comments. The web client sends a batch as one message
and then deletes it.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omnigent.entities import AgentTextComment, MessageData
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ, AuthProvider
from omnigent.server.routes._auth_helpers import require_access, require_user
from omnigent.server.routes._errors import session_not_found
from omnigent.stores import ConversationStore
from omnigent.stores.agent_text_comment_store import AgentTextCommentStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.util.uuid import InvalidUuidError, uuid_to_bytes

_MAX_TEXT_CHARS = 100_000
_MAX_CONTEXT_CHARS = 2_000
_MAX_BODY_CHARS = 20_000


class AddAgentTextCommentRequest(BaseModel):
    """A comment on a span of a completed agent message, with offsets in UTF-16 units."""

    conversation_item_id: str
    start_offset: int = Field(ge=0)
    end_offset: int
    selected_text: str = Field(max_length=_MAX_TEXT_CHARS)
    prefix_context: str = Field(default="", max_length=_MAX_CONTEXT_CHARS)
    suffix_context: str = Field(default="", max_length=_MAX_CONTEXT_CHARS)
    body: str = Field(max_length=_MAX_BODY_CHARS)

    model_config = ConfigDict(extra="forbid")

    @field_validator("conversation_item_id")
    @classmethod
    def _bare_item_id(cls, value: str) -> str:
        try:
            return uuid_to_bytes(value).hex()
        except InvalidUuidError as exc:
            raise ValueError("conversation_item_id must be an item id") from exc

    @model_validator(mode="after")
    def _validate_anchor(self) -> AddAgentTextCommentRequest:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        if not self.selected_text.strip():
            raise ValueError("selected_text must not be empty")
        if not self.body.strip():
            raise ValueError("body must not be empty")
        return self


class UpdateAgentTextCommentRequest(BaseModel):
    """A new body for one of the caller's comments."""

    body: str = Field(max_length=_MAX_BODY_CHARS)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_body(self) -> UpdateAgentTextCommentRequest:
        if not self.body.strip():
            raise ValueError("body must not be empty")
        return self


class DeleteAgentTextCommentsRequest(BaseModel):
    """The ids of the caller's comments to delete, e.g. a batch just sent."""

    comment_ids: list[str] = Field(max_length=500)

    model_config = ConfigDict(extra="forbid")


class AgentTextCommentResponse(BaseModel):
    """One of the caller's unsent comments on a completed agent message."""

    id: str
    conversation_id: str
    conversation_item_id: str
    start_offset: int
    end_offset: int
    selected_text: str
    prefix_context: str
    suffix_context: str
    body: str
    created_at: int
    updated_at: int


class AgentTextCommentDeleted(BaseModel):
    """A deleted comment."""

    deleted: bool = True


class AgentTextCommentsDeleted(BaseModel):
    """The ids of the caller's comments that were deleted."""

    deleted_comment_ids: list[str]


def create_agent_text_comments_router(
    store: AgentTextCommentStore,
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build the routes for the caller's unsent batch of comments on agent replies.

    :param store: Holds the comments.
    :param conversation_store: Finds the session and the commented message.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :param permission_store: Checks the caller's access to the session.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    async def caller(request: Request, session_id: str, level: int) -> str | None:
        user_id = require_user(request, auth_provider)
        if permission_store is not None:
            await require_access(user_id, session_id, level, permission_store, conversation_store)
        if await asyncio.to_thread(conversation_store.get_conversation, session_id) is None:
            raise session_not_found()
        return user_id

    @router.get(
        "/sessions/{session_id}/agent-text-comments",
        response_model=list[AgentTextCommentResponse],
    )
    async def list_agent_text_comments(
        request: Request, session_id: str
    ) -> list[AgentTextCommentResponse]:
        """List the caller's unsent comments in the session, oldest first."""
        user_id = await caller(request, session_id, LEVEL_READ)
        comments = await asyncio.to_thread(store.list, session_id, owner_user_id=user_id)
        return [_response(comment) for comment in comments]

    @router.post(
        "/sessions/{session_id}/agent-text-comments", response_model=AgentTextCommentResponse
    )
    async def add_agent_text_comment(
        request: Request, session_id: str, body: AddAgentTextCommentRequest
    ) -> AgentTextCommentResponse:
        """Add a comment on a completed agent message to the caller's batch."""
        user_id = await caller(request, session_id, LEVEL_EDIT)
        item = await asyncio.to_thread(
            conversation_store.get_item, session_id, body.conversation_item_id
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
                "Comments can only target completed agent text", code=ErrorCode.INVALID_INPUT
            )
        # The browser measures offsets in UTF-16 code units.
        if body.end_offset - body.start_offset != len(body.selected_text.encode("utf-16-le")) // 2:
            raise OmnigentError(
                "Selected text length does not match its offsets", code=ErrorCode.INVALID_INPUT
            )
        comment = await asyncio.to_thread(
            store.add,
            uuid.uuid4().hex,
            owner_user_id=user_id,
            conversation_id=session_id,
            conversation_item_id=item.id,
            start_offset=body.start_offset,
            end_offset=body.end_offset,
            selected_text=body.selected_text,
            prefix_context=body.prefix_context,
            suffix_context=body.suffix_context,
            body=body.body.strip(),
        )
        return _response(comment)

    @router.patch(
        "/sessions/{session_id}/agent-text-comments/{comment_id}",
        response_model=AgentTextCommentResponse,
    )
    async def update_agent_text_comment(
        request: Request, session_id: str, comment_id: str, body: UpdateAgentTextCommentRequest
    ) -> AgentTextCommentResponse:
        """Replace the body of one of the caller's comments."""
        user_id = await caller(request, session_id, LEVEL_EDIT)
        comment = await asyncio.to_thread(
            store.update,
            comment_id,
            owner_user_id=user_id,
            conversation_id=session_id,
            body=body.body.strip(),
        )
        if comment is None:
            raise OmnigentError("Agent text comment not found", code=ErrorCode.NOT_FOUND)
        return _response(comment)

    @router.delete(
        "/sessions/{session_id}/agent-text-comments/{comment_id}",
        response_model=AgentTextCommentDeleted,
    )
    async def delete_agent_text_comment(
        request: Request, session_id: str, comment_id: str
    ) -> AgentTextCommentDeleted:
        """Delete one of the caller's comments."""
        user_id = await caller(request, session_id, LEVEL_EDIT)
        deleted = await asyncio.to_thread(
            store.delete, [comment_id], owner_user_id=user_id, conversation_id=session_id
        )
        if not deleted:
            raise OmnigentError("Agent text comment not found", code=ErrorCode.NOT_FOUND)
        return AgentTextCommentDeleted()

    @router.post(
        "/sessions/{session_id}/agent-text-comments/delete-batch",
        response_model=AgentTextCommentsDeleted,
    )
    async def delete_agent_text_comments(
        request: Request, session_id: str, body: DeleteAgentTextCommentsRequest
    ) -> AgentTextCommentsDeleted:
        """Delete the caller's comments among the ids, e.g. a batch that was just sent."""
        user_id = await caller(request, session_id, LEVEL_EDIT)
        deleted = await asyncio.to_thread(
            store.delete, body.comment_ids, owner_user_id=user_id, conversation_id=session_id
        )
        return AgentTextCommentsDeleted(deleted_comment_ids=deleted)

    return router


def _response(comment: AgentTextComment) -> AgentTextCommentResponse:
    return AgentTextCommentResponse(
        id=comment.id,
        conversation_id=comment.conversation_id,
        conversation_item_id=comment.conversation_item_id,
        start_offset=comment.start_offset,
        end_offset=comment.end_offset,
        selected_text=comment.selected_text,
        prefix_context=comment.prefix_context,
        suffix_context=comment.suffix_context,
        body=comment.body,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )
