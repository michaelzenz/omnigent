"""Threaded reply anchored to one finalized agent text item."""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

AgentTextThreadState = Literal["queued", "running", "answered", "failed", "resolved"]


@dataclasses.dataclass
class AgentTextThread:
    """One durable, individually-sent comment thread on agent prose."""

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
