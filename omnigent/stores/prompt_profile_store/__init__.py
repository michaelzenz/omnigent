"""Prompt profile store interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import PromptProfile


class PromptProfileStore(ABC):
    """Persistence contract for workspace-scoped prompt profiles."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def create(
        self,
        profile_id: str,
        name: str,
        instructions: str,
        *,
        description: str | None = None,
        enabled: bool = True,
        visible: bool = True,
    ) -> PromptProfile: ...

    @abstractmethod
    def get(self, profile_id: str) -> PromptProfile | None: ...

    @abstractmethod
    def list(
        self,
        *,
        enabled_only: bool = False,
        visible_only: bool = True,
    ) -> list[PromptProfile]: ...

    @abstractmethod
    def update(self, profile_id: str, **fields: Any) -> PromptProfile | None: ...

    @abstractmethod
    def archive(self, profile_id: str) -> PromptProfile | None: ...
