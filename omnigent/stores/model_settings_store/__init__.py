"""Deployment-wide model settings persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSettings:
    """Configured harness models and policy-checking model."""

    harness_models: dict[str, list[str]]
    policy_model: str | None


class ModelSettingsStore(ABC):
    """Abstract store for the singleton deployment model settings."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self) -> ModelSettings:
        """Return the deployment model settings."""

    @abstractmethod
    def update(
        self,
        *,
        harness: str | None = None,
        enabled_models: list[str] | None = None,
        policy_model: str | None = None,
        update_policy_model: bool = False,
        updated_by: str | None = None,
    ) -> ModelSettings:
        """Update supplied fields and return the resulting settings."""


__all__ = ["ModelSettings", "ModelSettingsStore"]
