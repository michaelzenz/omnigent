"""Task agent role definition persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities.task_role_profile import TaskRoleProfile


class TaskRoleProfileStore(ABC):
    """Abstract base for task role definition persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self, role: str) -> TaskRoleProfile | None:
        """Return the definition for ``role``, or ``None`` if unset."""

    @abstractmethod
    def list_roles(self, *, kind: str | None = None) -> list[TaskRoleProfile]:
        """List role definitions, optionally filtered to one kind."""

    @abstractmethod
    def delete(self, role: str) -> bool:
        """Delete a role definition. Returns ``False`` when missing."""

    @abstractmethod
    def upsert(
        self,
        role: str,
        *,
        kind: str | None = None,
        agent_profile_id: str | None = None,
        prompt_profile_id: str | None = None,
        harness: str | None = None,
        model: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
        description: str | None = None,
        clear_model: bool = False,
    ) -> TaskRoleProfile:
        """
        Create or update a role definition.

        ``None`` leaves a field unchanged. To clear ``model`` back to ``None``
        (the harness picks its own model), pass ``clear_model=True``. ``kind``
        defaults to the family implied by the role key. Pass an empty string
        for ``description`` to clear it back to ``None``.
        """
