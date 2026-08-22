"""Persistence contract for PuppyGarden worker providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities.worker_provider import WorkerProvider


class WorkerProviderStore(ABC):
    """Workspace-scoped definitions used to initialize workers."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self, provider_id: str) -> WorkerProvider | None: ...

    @abstractmethod
    def list(self) -> list[WorkerProvider]: ...

    @abstractmethod
    def create(
        self,
        provider_id: str,
        name: str,
        kind: str,
        configuration: str,
        *,
        description: str | None = None,
        built_in: bool = False,
    ) -> WorkerProvider: ...

    @abstractmethod
    def update(self, provider_id: str, **fields: Any) -> WorkerProvider | None: ...

    @abstractmethod
    def delete(self, provider_id: str) -> bool: ...
