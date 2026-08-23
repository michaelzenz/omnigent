"""Comment anchored to one finalized agent text item."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class AgentTextComment:
    """An unsent review comment on rendered agent prose."""

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
