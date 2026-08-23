"""Routes for individually-sent threads anchored to finalized agent responses."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, model_validator

from omnigent.entities import MessageData
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.execution_targets import is_omniharness_agent
from omnigent.harness_aliases import canonicalize_harness
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_access
from omnigent.server.routes._errors import session_not_found
from omnigent.server.routes._sessions.helpers import _resolve_harness_impl
from omnigent.stores import AgentStore, ConversationStore
from omnigent.stores.permission_store import PermissionStore


class AddAgentTextThreadRequest(BaseModel):
    client_request_id: str
    source_item_id: str
    start_offset: int
    end_offset: int
    selected_text: str
    prefix_context: str = ""
    suffix_context: str = ""
    comment: str

    @model_validator(mode="after")
    def validate_request(self) -> AddAgentTextThreadRequest:
        if not self.client_request_id.strip():
            raise ValueError("client_request_id must not be empty")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("invalid selected-text offsets")
        if not self.selected_text.strip() or not self.comment.strip():
            raise ValueError("selected_text and comment must not be empty")
        return self


class AddAgentTextThreadTurnRequest(BaseModel):
    client_request_id: str
    question: str
    selected_quote: str | None = None

    @model_validator(mode="after")
    def validate_request(self) -> AddAgentTextThreadTurnRequest:
        if not self.client_request_id.strip() or not self.question.strip():
            raise ValueError("client_request_id and question must not be empty")
        return self


class FailAgentTextThreadRequest(BaseModel):
    message: str


def create_agent_text_threads_router(
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    agent_cache: AgentCache,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build routes for durable, individually-sent agent-text threads."""
    router = APIRouter()

    async def require_session_access(user_id: str | None, session_id: str, level: int) -> None:
        if permission_store is not None:
            await require_access(
                user_id,
                session_id,
                level,
                permission_store,
                conversation_store,
            )
        if await asyncio.to_thread(conversation_store.get_conversation, session_id) is None:
            raise session_not_found()

    async def threaded_supported(session_id: str) -> bool:
        conversation = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conversation is None or conversation.agent_id is None:
            return False
        agent = await asyncio.to_thread(agent_store.get, conversation.agent_id)
        if is_omniharness_agent(agent):
            return True
        harness = await asyncio.to_thread(
            _resolve_harness_impl,
            conversation,
            agent_store=agent_store,
            agent_cache=agent_cache,
        )
        return canonicalize_harness(harness) == "openai-agents"

    async def require_threaded_support(session_id: str) -> None:
        if not await threaded_supported(session_id):
            raise OmnigentError(
                "Threaded replies require OmniHarness or OpenAI Agents SDK",
                code=ErrorCode.CONFLICT,
            )

    @router.get("/sessions/{session_id}/agent-text-threads/capability")
    async def get_capability(request: Request, session_id: str) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_READ)
        supported = await threaded_supported(session_id)
        return {
            "supported": supported,
            "reason": None if supported else "unsupported_harness",
        }

    @router.get("/sessions/{session_id}/agent-text-threads")
    async def list_threads(
        request: Request,
        session_id: str,
        state: Literal["open", "resolved"] = "open",
    ) -> list[dict[str, Any]]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_READ)
        threads = await asyncio.to_thread(
            conversation_store.list_agent_text_threads,
            session_id,
            resolved=state == "resolved",
        )
        return [asdict(thread) for thread in threads]

    @router.post("/sessions/{session_id}/agent-text-threads")
    async def add_thread(
        request: Request,
        session_id: str,
        body: AddAgentTextThreadRequest,
    ) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        await require_threaded_support(session_id)
        item = await asyncio.to_thread(
            conversation_store.get_item,
            session_id,
            body.source_item_id,
        )
        if (
            item is None
            or item.type != "message"
            or not isinstance(item.data, MessageData)
            or item.data.role != "assistant"
            or item.status != "completed"
            or item.data.is_meta
        ):
            raise OmnigentError(
                "Threaded comments can only target completed agent text",
                code=ErrorCode.INVALID_INPUT,
            )
        selected_utf16_length = len(body.selected_text.encode("utf-16-le")) // 2
        if body.end_offset - body.start_offset != selected_utf16_length:
            raise OmnigentError(
                "Selected text length does not match its offsets",
                code=ErrorCode.INVALID_INPUT,
            )
        try:
            thread = await asyncio.to_thread(
                conversation_store.add_agent_text_thread,
                session_id,
                body.source_item_id,
                client_request_id=body.client_request_id,
                start_offset=body.start_offset,
                end_offset=body.end_offset,
                selected_text=body.selected_text,
                prefix_context=body.prefix_context,
                suffix_context=body.suffix_context,
                user_comment=body.comment.strip(),
            )
        except LookupError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.NOT_FOUND) from exc
        return asdict(thread)

    @router.post("/sessions/{session_id}/agent-text-threads/{thread_id}/turns")
    async def add_turn(
        request: Request,
        session_id: str,
        thread_id: str,
        body: AddAgentTextThreadTurnRequest,
    ) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        await require_threaded_support(session_id)
        thread = await asyncio.to_thread(
            conversation_store.get_agent_text_thread,
            session_id,
            thread_id,
        )
        if thread is None or thread.resolved_at is not None:
            raise OmnigentError("Open agent text thread not found", code=ErrorCode.NOT_FOUND)
        if thread.state == "failed":
            raise OmnigentError(
                "Retry the original comment before adding follow-ups",
                code=ErrorCode.CONFLICT,
            )
        try:
            turn = await asyncio.to_thread(
                conversation_store.add_agent_text_thread_turn,
                session_id,
                thread_id,
                client_request_id=body.client_request_id,
                question=body.question.strip(),
                selected_quote=(body.selected_quote or "").strip() or None,
            )
        except LookupError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.NOT_FOUND) from exc
        return asdict(turn)

    @router.post("/sessions/{session_id}/agent-text-threads/{thread_id}/resolve")
    async def resolve_thread(request: Request, session_id: str, thread_id: str) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        open_threads = await asyncio.to_thread(
            conversation_store.list_agent_text_threads,
            session_id,
            resolved=False,
        )
        existing = next((thread for thread in open_threads if thread.id == thread_id), None)
        if existing is None:
            raise OmnigentError("Agent text thread not found", code=ErrorCode.NOT_FOUND)
        if existing.state != "answered" or any(
            turn.state in {"queued", "submitting", "running"} for turn in existing.turns
        ):
            raise OmnigentError(
                "Threads can only be resolved after all responses finish",
                code=ErrorCode.CONFLICT,
            )
        thread = await asyncio.to_thread(
            conversation_store.resolve_agent_text_thread,
            session_id,
            thread_id,
        )
        assert thread is not None
        return asdict(thread)

    @router.post("/sessions/{session_id}/agent-text-threads/{thread_id}/fail")
    async def fail_thread(
        request: Request,
        session_id: str,
        thread_id: str,
        body: FailAgentTextThreadRequest,
    ) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        existing = await asyncio.to_thread(
            conversation_store.get_agent_text_thread,
            session_id,
            thread_id,
        )
        if existing is None:
            raise OmnigentError("Agent text thread not found", code=ErrorCode.NOT_FOUND)
        if existing.state not in {"queued", "running"}:
            raise OmnigentError(
                "Only queued or running threads can fail",
                code=ErrorCode.CONFLICT,
            )
        thread = await asyncio.to_thread(
            conversation_store.fail_agent_text_thread,
            session_id,
            thread_id,
            body.message.strip() or "Threaded reply failed",
        )
        assert thread is not None
        return asdict(thread)

    @router.post("/sessions/{session_id}/agent-text-threads/{thread_id}/retry")
    async def retry_thread(request: Request, session_id: str, thread_id: str) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        await require_threaded_support(session_id)
        open_threads = await asyncio.to_thread(
            conversation_store.list_agent_text_threads,
            session_id,
            resolved=False,
        )
        existing = next((thread for thread in open_threads if thread.id == thread_id), None)
        if existing is None:
            raise OmnigentError("Agent text thread not found", code=ErrorCode.NOT_FOUND)
        if existing.state != "failed":
            raise OmnigentError(
                "Only failed threads can be retried",
                code=ErrorCode.CONFLICT,
            )
        thread = await asyncio.to_thread(
            conversation_store.retry_agent_text_thread,
            session_id,
            thread_id,
        )
        assert thread is not None
        return asdict(thread)

    @router.post("/sessions/{session_id}/agent-text-threads/{thread_id}/turns/{turn_id}/fail")
    async def fail_turn(
        request: Request,
        session_id: str,
        thread_id: str,
        turn_id: str,
        body: FailAgentTextThreadRequest,
    ) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        turn = await asyncio.to_thread(
            conversation_store.get_agent_text_thread_turn, session_id, turn_id
        )
        if turn is None or turn.thread_id != thread_id:
            raise OmnigentError("Thread turn not found", code=ErrorCode.NOT_FOUND)
        if turn.state not in {"queued", "submitting", "running"}:
            raise OmnigentError("Thread turn is not active", code=ErrorCode.CONFLICT)
        result = await asyncio.to_thread(
            conversation_store.fail_agent_text_thread_turn,
            session_id,
            turn_id,
            body.message.strip() or "Threaded reply failed",
        )
        assert result is not None
        return asdict(result)

    @router.post("/sessions/{session_id}/agent-text-threads/{thread_id}/turns/{turn_id}/retry")
    async def retry_turn(
        request: Request,
        session_id: str,
        thread_id: str,
        turn_id: str,
    ) -> dict[str, Any]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        await require_threaded_support(session_id)
        open_threads = await asyncio.to_thread(
            conversation_store.list_agent_text_threads,
            session_id,
            resolved=False,
        )
        parent = next((thread for thread in open_threads if thread.id == thread_id), None)
        turn = next(
            (item for item in (parent.turns if parent else []) if item.id == turn_id), None
        )
        if turn is None or turn.state != "failed":
            raise OmnigentError("Failed thread turn not found", code=ErrorCode.NOT_FOUND)
        result = await asyncio.to_thread(
            conversation_store.retry_agent_text_thread_turn, session_id, turn_id
        )
        if result is None:
            raise OmnigentError("Thread turn is no longer failed", code=ErrorCode.CONFLICT)
        return asdict(result)

    @router.delete("/sessions/{session_id}/agent-text-threads/{thread_id}")
    async def delete_thread(request: Request, session_id: str, thread_id: str) -> dict[str, bool]:
        user_id = get_user_id(request, auth_provider)
        await require_session_access(user_id, session_id, LEVEL_EDIT)
        existing = await asyncio.to_thread(
            conversation_store.get_agent_text_thread,
            session_id,
            thread_id,
        )
        if existing is None:
            raise OmnigentError("Agent text thread not found", code=ErrorCode.NOT_FOUND)
        if not (
            existing.state == "failed"
            or (existing.state == "queued" and existing.response_id is None)
        ):
            raise OmnigentError(
                "Submitted or answered threads cannot be deleted",
                code=ErrorCode.CONFLICT,
            )
        deleted = await asyncio.to_thread(
            conversation_store.delete_agent_text_thread,
            session_id,
            thread_id,
        )
        assert deleted
        return {"deleted": True}

    return router
