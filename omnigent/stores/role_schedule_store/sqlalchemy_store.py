"""SQLAlchemy-backed GlobalHub role schedule store."""

from __future__ import annotations

import builtins
import uuid
from typing import cast

from sqlalchemy import ColumnElement, and_, asc, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlRoleSchedule, current_workspace_id
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import RoleSchedule
from omnigent.stores.role_schedule_store import RoleScheduleStore
from omnigent.util.uuid import normalize_uuid


def _to_entity(row: SqlRoleSchedule) -> RoleSchedule:
    return RoleSchedule(
        id=row.id,
        owner_user_id=row.owner_user_id,
        role_key=row.role_key,
        rrule=row.rrule,
        timezone=row.timezone,
        next_run_at=row.next_run_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


def _owned(role_key: str, owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlRoleSchedule.workspace_id == current_workspace_id(),
        SqlRoleSchedule.owner_user_id == owner_user_id,
        SqlRoleSchedule.role_key == role_key,
    )


class SqlAlchemyRoleScheduleStore(RoleScheduleStore):
    """
    SQLAlchemy-backed implementation of :class:`RoleScheduleStore`.

    Every query except the due scan is scoped by ``workspace_id`` and
    ``owner_user_id``.
    """

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: SQLAlchemy database URI, e.g.
            ``"sqlite:///chat.db"``.
        """
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.role_schedule_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.role_schedule_store",
            immediate=True,
        )

    def get(self, role_key: str, *, owner_user_id: str | None) -> RoleSchedule | None:
        """Return the owner's schedule for ``role_key``, or ``None`` before the owner saves one."""
        stmt = select(SqlRoleSchedule).where(_owned(role_key, owner_user_id)).limit(1)
        with self._session("select_role_schedule") as session:
            row = session.execute(stmt).scalars().first()
            return _to_entity(row) if row is not None else None

    def save(
        self,
        role_key: str,
        *,
        owner_user_id: str | None,
        rrule: str,
        timezone: str,
        next_run_at: int | None,
    ) -> RoleSchedule:
        """Create or replace the owner's schedule for a role."""
        now = now_epoch()

        def write(session: Session) -> RoleSchedule:
            require_active_account(session, owner_user_id)
            row = session.execute(
                select(SqlRoleSchedule).where(_owned(role_key, owner_user_id)).limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = SqlRoleSchedule(
                    id=uuid.uuid4().hex,
                    owner_user_id=owner_user_id,
                    role_key=role_key,
                    created_at=now,
                )
                session.add(row)
            else:
                row.updated_at = now
            row.rrule = rrule
            row.timezone = timezone
            row.next_run_at = next_run_at
            return _to_entity(row)

        try:
            return run_write_transaction(self._session_immediate, "save_role_schedule", write)
        except IntegrityError:
            # A concurrent save can insert the owner's schedule for the role first.
            # Retry in a fresh transaction, which updates that schedule.
            return run_write_transaction(self._session_immediate, "save_role_schedule", write)

    def delete(self, role_key: str, *, owner_user_id: str | None) -> bool:
        """Delete the owner's schedule for a role."""

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            result = cast(
                CursorResult[tuple[object]],
                session.execute(
                    delete(SqlRoleSchedule)
                    .where(_owned(role_key, owner_user_id))
                    .execution_options(synchronize_session=False)
                ),
            )
            return result.rowcount > 0

        return run_write_transaction(self._session_immediate, "delete_role_schedule", write)

    def list_due_all_workspaces(
        self, *, now: int, limit: int = 100
    ) -> builtins.list[RoleSchedule]:
        """List the schedules in every workspace whose next run is due at ``now``."""
        stmt = (
            select(SqlRoleSchedule)
            .where(SqlRoleSchedule.next_run_at <= now)
            .order_by(asc(SqlRoleSchedule.next_run_at), asc(SqlRoleSchedule.id))
            .limit(limit)
        )
        with self._session("list_due_role_schedules_in_all_workspaces") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars()]

    def advance(self, schedule_id: str, *, due_at: int, next_run_at: int | None) -> bool:
        """Move a due schedule in the current workspace to its next run."""
        now = now_epoch()

        def write(session: Session) -> bool:
            result = cast(
                CursorResult[tuple[object]],
                session.execute(
                    update(SqlRoleSchedule)
                    .where(
                        SqlRoleSchedule.workspace_id == current_workspace_id(),
                        SqlRoleSchedule.id == normalize_uuid(schedule_id),
                        SqlRoleSchedule.next_run_at == due_at,
                    )
                    .values(next_run_at=next_run_at, updated_at=now)
                    .execution_options(synchronize_session=False)
                ),
            )
            return result.rowcount == 1

        return run_write_transaction(self._session_immediate, "advance_role_schedule", write)
