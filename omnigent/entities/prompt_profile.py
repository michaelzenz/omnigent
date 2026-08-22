"""Plain-text prompt profile entity."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptProfile:
    """Workspace-scoped instructions selectable for OmniHarness turns."""

    id: str
    name: str
    instructions: str
    created_at: int
    description: str | None = None
    enabled: bool = True
    visible: bool = True
    updated_at: int | None = None
