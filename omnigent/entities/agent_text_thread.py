"""Threaded replies anchored to finalized agent text."""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

AgentTextThreadState = Literal["queued", "running", "answered", "failed", "resolved"]
AgentTextThreadTurnState = Literal["queued", "submitting", "running", "answered", "failed"]


@dataclasses.dataclass
class AgentTextThreadTurn:
    """One user question and its paired agent response inside a thread."""

    id: str
    thread_id: str
    sequence: int
    client_request_id: str
    submission_id: str
    question: str
    selected_quote: str | None
    state: AgentTextThreadTurnState
    user_item_id: str | None
    response_id: str | None
    failure_message: str | None
    created_at: int
    updated_at: int
    items: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class AgentTextThread:
    """One durable comment thread anchored to agent prose."""

    id: str
    conversation_id: str
    source_item_id: str
    start_offset: int
    end_offset: int
    selected_text: str
    prefix_context: str
    suffix_context: str
    user_comment: str
    state: AgentTextThreadState
    user_item_id: str | None
    response_id: str | None
    failure_message: str | None
    resolved_at: int | None
    created_at: int
    updated_at: int
    source_position: int | None = None
    items: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    turns: list[AgentTextThreadTurn] = dataclasses.field(default_factory=list)
