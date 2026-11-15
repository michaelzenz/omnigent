"""GlobalHub role schedule persistence.

Each user has at most one schedule per role: when the role's session gets its
scheduled run message. Reads and writes are scoped to the schedule's owner,
except the dispatcher's due scan, which reads every workspace.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod

from omnigent.entities import RoleSchedule


class RoleScheduleStore(ABC):
    """Abstract base for GlobalHub role schedule persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def get(self, role_key: str, *, owner_user_id: str | None) -> RoleSchedule | None:
        """Return the owner's schedule for ``role_key``, or ``None`` before the owner saves one."""

    @abstractmethod
    def save(
        self,
        role_key: str,
        *,
        owner_user_id: str | None,
        rrule: str,
        timezone: str,
        next_run_at: int | None,
    ) -> RoleSchedule:
        """
        Create or replace the owner's schedule for a role.

        :param role_key: The role that runs, e.g. ``"custodian"``.
        :param owner_user_id: The owner, or ``None`` in single-user mode.
        :param rrule: An RFC 5545 recurrence rule without an end.
        :param timezone: An IANA timezone, e.g. ``"America/Los_Angeles"``.
        :param next_run_at: Unix epoch seconds of the next run, or ``None`` to
            turn the schedule off.
        :returns: The saved schedule.
        """

    @abstractmethod
    def delete(self, role_key: str, *, owner_user_id: str | None) -> bool:
        """
        Delete the owner's schedule for a role.

        :returns: ``False`` when the owner had no schedule for the role.
        """

    @abstractmethod
    def list_due_all_workspaces(
        self, *, now: int, limit: int = 100
    ) -> builtins.list[RoleSchedule]:
        """
        List the schedules in every workspace whose next run is due at ``now``.

        The dispatcher runs outside a request, so it acts on each schedule
        inside the schedule's ``workspace_scope``.

        :param now: Current Unix epoch seconds.
        :param limit: At most this many schedules, the earliest due first.
        """

    @abstractmethod
    def advance(self, schedule_id: str, *, due_at: int, next_run_at: int | None) -> bool:
        """
        Move a due schedule in the current workspace to its next run.

        Only the caller that moves it may queue the run, so a run is sent once
        even when two dispatcher passes find it.

        :param schedule_id: The schedule's id.
        :param due_at: The ``next_run_at`` the caller found.
        :param next_run_at: The next run in Unix epoch seconds, or ``None`` to
            turn the schedule off.
        :returns: ``False`` when the schedule changed since the caller found it.
        """
