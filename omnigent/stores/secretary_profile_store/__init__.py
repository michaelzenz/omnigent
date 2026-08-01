"""Per-user secretary profile persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities.secretary import UserSecretaryProfile


class SecretaryProfileStore(ABC):
    """Abstract base for secretary profile persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self, user_id: str) -> UserSecretaryProfile | None:
        """Return the profile for ``user_id``, or ``None`` if unset."""

    @abstractmethod
    def upsert(
        self,
        user_id: str,
        *,
        agent_profile_id: str | None = None,
        conversation_id: str | None = None,
        harness: str | None = None,
        model: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
        clear_conversation_id: bool = False,
    ) -> UserSecretaryProfile:
        """Create or update a secretary profile."""
