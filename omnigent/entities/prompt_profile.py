"""Plain-text prompt profile entity."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptProfile:
    """Workspace-scoped instructions selectable for Omnigent turns."""

    id: str
    name: str
    instructions: str
    created_at: int
    description: str | None = None
    enabled: bool = True
    archived: bool = False
    updated_at: int | None = None
