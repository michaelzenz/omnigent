"""GlobalHub custodian sweep persistence.

Each user has at most one sweep: when their custodian gets its daily session
cleanup message. Reads and writes are scoped to the sweep's owner, except the
dispatcher's due scan, which reads every workspace.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod

from omnigent.entities import CustodianSweep


class CustodianSweepStore(ABC):
    """Abstract base for GlobalHub custodian sweep persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def get(self, *, owner_user_id: str | None) -> CustodianSweep | None:
        """Return the owner's sweep, or ``None`` before the owner saves one."""

    @abstractmethod
    def save(
        self,
        *,
        owner_user_id: str | None,
        local_time: str,
        timezone: str,
        next_run_at: int | None,
    ) -> CustodianSweep:
        """
        Create or replace the owner's sweep.

        :param owner_user_id: The owner, or ``None`` in single-user mode.
        :param local_time: The time of day in ``timezone``, as ``"HH:MM"``.
        :param timezone: An IANA timezone, e.g. ``"America/Los_Angeles"``.
        :param next_run_at: Unix epoch seconds of the next message, or ``None``
            to turn the sweep off.
        :returns: The saved sweep.
        """

    @abstractmethod
    def list_due_all_workspaces(
        self, *, now: int, limit: int = 100
    ) -> builtins.list[CustodianSweep]:
        """
        List the sweeps in every workspace whose next message is due at ``now``.

        The dispatcher runs outside a request, so it acts on each sweep inside
        the sweep's ``workspace_scope``.

        :param now: Current Unix epoch seconds.
        :param limit: At most this many sweeps, the earliest due first.
        """

    @abstractmethod
    def advance(self, sweep_id: str, *, due_at: int, next_run_at: int) -> bool:
        """
        Move a due sweep in the current workspace to its next time.

        Only the caller that moves it may queue the message, so a sweep is sent
        once even when two dispatcher passes find it.

        :param sweep_id: The sweep's id.
        :param due_at: The ``next_run_at`` the caller found.
        :param next_run_at: The sweep's next time, in Unix epoch seconds.
        :returns: ``False`` when the sweep changed since the caller found it.
        """
