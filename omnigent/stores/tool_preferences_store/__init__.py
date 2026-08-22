"""Deployment-wide tool preference persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolPreferences:
    """Configured deployment-wide tool preferences."""

    disabled_tools: frozenset[str]


class ToolPreferencesStore(ABC):
    """Abstract store for the singleton deployment tool preferences."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self) -> ToolPreferences:
        """Return the deployment tool preferences."""

    @abstractmethod
    def update(
        self,
        *,
        disabled_tools: list[str],
        updated_by: str | None = None,
    ) -> ToolPreferences:
        """Replace the disabled-tools set and return the resulting preferences."""


__all__ = ["ToolPreferences", "ToolPreferencesStore"]
