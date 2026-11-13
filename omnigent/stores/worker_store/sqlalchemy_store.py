"""SQLAlchemy-backed GlobalHub worker store."""

from __future__ import annotations

import builtins
from collections.abc import Collection

from sqlalchemy import ColumnElement, and_, asc, select
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlWorker, current_workspace_id
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import Worker
from omnigent.stores.worker_store import UNSET, WorkerStore


def _to_entity(row: SqlWorker) -> Worker:
    return Worker(
        id=row.id,
        task_id=row.task_id,
        owner_user_id=row.owner_user_id,
        session_id=row.session_id,
        created_at=row.created_at,
        title=row.title,
        updated_at=row.updated_at,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlWorker.workspace_id == current_workspace_id(),
        SqlWorker.owner_user_id == owner_user_id,
    )


def _owned_row(session: Session, worker_id: str, owner_user_id: str | None) -> SqlWorker | None:
    row = session.get(SqlWorker, (current_workspace_id(), worker_id))
    return row if row is not None and row.owner_user_id == owner_user_id else None


class SqlAlchemyWorkerStore(WorkerStore):
    """
    SQLAlchemy-backed implementation of :class:`WorkerStore`.

    Every query is scoped by ``workspace_id`` and ``owner_user_id``.
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
            query_name_prefix="omnigent.worker_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.worker_store",
            immediate=True,
        )

    def create(
        self,
        worker_id: str,
        *,
        owner_user_id: str | None,
        task_id: str,
        session_id: str,
        title: str | None = None,
    ) -> Worker:
        """Attach a session to a task."""
        now = now_epoch()

        def write(session: Session) -> Worker:
            require_active_account(session, owner_user_id)
            row = SqlWorker(
                id=worker_id,
                task_id=task_id,
                owner_user_id=owner_user_id,
                session_id=session_id,
                title=title,
                created_at=now,
                updated_at=None,
            )
            session.add(row)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "insert_worker", write)

    def get(self, worker_id: str, *, owner_user_id: str | None) -> Worker | None:
        """Return the owner's worker, or ``None`` when missing or someone else's."""
        with self._session("select_worker_by_id") as session:
            row = _owned_row(session, worker_id, owner_user_id)
            return _to_entity(row) if row is not None else None

    def get_for_task_and_session(
        self, task_id: str, session_id: str, *, owner_user_id: str | None
    ) -> Worker | None:
        """Return the owner's worker for ``session_id`` on ``task_id``, or ``None``."""
        stmt = select(SqlWorker).where(
            _owned(owner_user_id),
            SqlWorker.task_id == task_id,
            SqlWorker.session_id == session_id,
        )
        with self._session("select_worker_by_task_and_session") as session:
            row = session.execute(stmt).scalars().first()
            return _to_entity(row) if row is not None else None

    def list_for_tasks(
        self, task_ids: Collection[str], *, owner_user_id: str | None
    ) -> builtins.list[Worker]:
        """List the owner's workers on these tasks, by task and then attach order."""
        if not task_ids:
            return []
        stmt = (
            select(SqlWorker)
            .where(_owned(owner_user_id), SqlWorker.task_id.in_(task_ids))
            .order_by(asc(SqlWorker.task_id), asc(SqlWorker.created_at), asc(SqlWorker.id))
        )
        with self._session("list_workers_for_tasks") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def update(
        self,
        worker_id: str,
        *,
        owner_user_id: str | None,
        title: str | None = UNSET,
    ) -> Worker | None:
        """Retitle the owner's worker."""

        def write(session: Session) -> Worker | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, worker_id, owner_user_id)
            if row is None:
                return None
            if title is not UNSET and row.title != title:
                row.title = title
                row.updated_at = now_epoch()
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "update_worker", write)

    def delete(self, worker_id: str, *, owner_user_id: str | None) -> bool:
        """Detach the owner's worker from its task; return whether it existed."""

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, worker_id, owner_user_id)
            if row is None:
                return False
            session.delete(row)
            return True

        return run_write_transaction(self._session_immediate, "delete_worker", write)
