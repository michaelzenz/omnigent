"""Persistence contract for categorized user memory."""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities.memory import MemoryCategory

DEFAULT_MEMORY_CATEGORY_NAMES = (
    "Abbreviations",
    "Work & team",
    "Personal",
    "Preferences",
)


class MemoryStore(ABC):
    """Owner-private memory category CRUD."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def list(self, *, user_id: str | None, seed_defaults: bool = True) -> list[MemoryCategory]:
        """List categories in display order, optionally seeding defaults."""
        ...

    @abstractmethod
    def create(
        self,
        category_id: str,
        *,
        user_id: str | None,
        name: str,
        content: str = "",
        display_order: int | None = None,
    ) -> MemoryCategory:
        """Create a category."""
        ...

    @abstractmethod
    def update(
        self,
        category_id: str,
        *,
        user_id: str | None,
        name: str | None = None,
        content: str | None = None,
        display_order: int | None = None,
    ) -> MemoryCategory | None:
        """Update an owned category."""
        ...

    @abstractmethod
    def delete(self, category_id: str, *, user_id: str | None) -> bool:
        """Delete an owned category."""
        ...

    @abstractmethod
    def reorder(self, category_ids: list[str], *, user_id: str | None) -> list[MemoryCategory]:
        """Replace the owner's category order."""
        ...

    @abstractmethod
    def get_max_tokens(self, *, user_id: str | None, default: int) -> int:
        """Return the owner's configured memory limit or the deployment default."""
        ...

    @abstractmethod
    def set_max_tokens(self, max_tokens: int, *, user_id: str | None) -> int:
        """Persist and return the owner's memory limit."""
        ...
