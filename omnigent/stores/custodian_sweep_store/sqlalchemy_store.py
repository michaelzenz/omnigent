"""SQLAlchemy-backed GlobalHub custodian sweep store."""

from __future__ import annotations

import builtins
import uuid
from typing import cast

from sqlalchemy import ColumnElement, and_, asc, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlCustodianSweep, current_workspace_id
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import CustodianSweep
from omnigent.stores.custodian_sweep_store import CustodianSweepStore
from omnigent.util.uuid import normalize_uuid


def _to_entity(row: SqlCustodianSweep) -> CustodianSweep:
    return CustodianSweep(
        id=row.id,
        owner_user_id=row.owner_user_id,
        local_time=row.local_time,
        timezone=row.timezone,
        next_run_at=row.next_run_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlCustodianSweep.workspace_id == current_workspace_id(),
        SqlCustodianSweep.owner_user_id == owner_user_id,
    )


class SqlAlchemyCustodianSweepStore(CustodianSweepStore):
    """
    SQLAlchemy-backed implementation of :class:`CustodianSweepStore`.

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
            query_name_prefix="omnigent.custodian_sweep_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.custodian_sweep_store",
            immediate=True,
        )

    def get(self, *, owner_user_id: str | None) -> CustodianSweep | None:
        """Return the owner's sweep, or ``None`` before the owner saves one."""
        stmt = select(SqlCustodianSweep).where(_owned(owner_user_id)).limit(1)
        with self._session("select_custodian_sweep") as session:
            row = session.execute(stmt).scalars().first()
            return _to_entity(row) if row is not None else None

    def save(
        self,
        *,
        owner_user_id: str | None,
        local_time: str,
        timezone: str,
        next_run_at: int | None,
    ) -> CustodianSweep:
        """Create or replace the owner's sweep."""
        now = now_epoch()

        def write(session: Session) -> CustodianSweep:
            require_active_account(session, owner_user_id)
            row = session.execute(
                select(SqlCustodianSweep).where(_owned(owner_user_id)).limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = SqlCustodianSweep(
                    id=uuid.uuid4().hex,
                    owner_user_id=owner_user_id,
                    created_at=now,
                )
                session.add(row)
            else:
                row.updated_at = now
            row.local_time = local_time
            row.timezone = timezone
            row.next_run_at = next_run_at
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "save_custodian_sweep", write)

    def list_due_all_workspaces(
        self, *, now: int, limit: int = 100
    ) -> builtins.list[CustodianSweep]:
        """List the sweeps in every workspace whose next message is due at ``now``."""
        stmt = (
            select(SqlCustodianSweep)
            .where(SqlCustodianSweep.next_run_at <= now)
            .order_by(asc(SqlCustodianSweep.next_run_at), asc(SqlCustodianSweep.id))
            .limit(limit)
        )
        with self._session("list_due_custodian_sweeps_in_all_workspaces") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars()]

    def advance(self, sweep_id: str, *, due_at: int, next_run_at: int) -> bool:
        """Move a due sweep in the current workspace to its next time."""
        now = now_epoch()

        def write(session: Session) -> bool:
            result = cast(
                CursorResult[tuple[object]],
                session.execute(
                    update(SqlCustodianSweep)
                    .where(
                        SqlCustodianSweep.workspace_id == current_workspace_id(),
                        SqlCustodianSweep.id == normalize_uuid(sweep_id),
                        SqlCustodianSweep.next_run_at == due_at,
                    )
                    .values(next_run_at=next_run_at, updated_at=now)
                    .execution_options(synchronize_session=False)
                ),
            )
            return result.rowcount == 1

        return run_write_transaction(self._session_immediate, "advance_custodian_sweep", write)
