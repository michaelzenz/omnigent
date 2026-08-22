"""Persistent categorized user memory entities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryCategory:
    """One owner-private memory document."""

    id: str
    name: str
    user_id: str | None
    display_order: int
    content: str
    token_count: int
    created_at: int
    updated_at: int | None = None
