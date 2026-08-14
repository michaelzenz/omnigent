"""SQLAlchemy-backed worker store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, select

from omnigent.db.db_models import SqlWorker, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import Worker
from omnigent.stores.worker_store import WORKER_KIND_MANAGED, WorkerStore

_UNSET: Any = object()
_WORKER_KINDS = frozenset({WORKER_KIND_MANAGED, "external"})


def _worker_to_entity(row: SqlWorker) -> Worker:
    return Worker(
        id=row.id,
        task_id=row.task_id,
        kind=row.kind,
        role_key=row.role_key,
        agent_profile_id=row.agent_profile_id,
        session_id=row.session_id,
        external_session_hint=row.external_session_hint,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyWorkerStore(WorkerStore):
    """SQLAlchemy-backed implementation of :class:`WorkerStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)

    def create_worker(
        self,
        worker_id: str,
        task_id: str,
        *,
        kind: str = WORKER_KIND_MANAGED,
        role_key: str | None = None,
        agent_profile_id: str | None = None,
        session_id: str | None = None,
        external_session_hint: str | None = None,
    ) -> Worker:
        if kind not in _WORKER_KINDS:
            raise ValueError(f"unknown worker kind: {kind!r}")
        row = SqlWorker(
            id=worker_id,
            task_id=task_id,
            kind=kind,
            role_key=role_key,
            agent_profile_id=agent_profile_id,
            session_id=session_id,
            external_session_hint=external_session_hint,
            created_at=now_epoch(),
            updated_at=None,
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _worker_to_entity(row)

    def get_worker(self, worker_id: str) -> Worker | None:
        with self._session() as session:
            row = session.get(SqlWorker, (current_workspace_id(), worker_id))
            if row is None:
                return None
            return _worker_to_entity(row)

    def get_by_session_id(self, session_id: str) -> Worker | None:
        with self._session() as session:
            stmt = (
                select(SqlWorker)
                .where(SqlWorker.workspace_id == current_workspace_id())
                .where(SqlWorker.session_id == session_id)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _worker_to_entity(row)

    def get_by_external_hint(self, external_session_hint: str) -> Worker | None:
        with self._session() as session:
            stmt = (
                select(SqlWorker)
                .where(SqlWorker.workspace_id == current_workspace_id())
                .where(SqlWorker.external_session_hint == external_session_hint)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _worker_to_entity(row)

    def list_workers_for_task(self, task_id: str) -> list[Worker]:
        with self._session() as session:
            stmt = (
                select(SqlWorker)
                .where(SqlWorker.workspace_id == current_workspace_id())
                .where(SqlWorker.task_id == task_id)
                .order_by(asc(SqlWorker.created_at), asc(SqlWorker.id))
            )
            rows = session.execute(stmt).scalars().all()
            return [_worker_to_entity(row) for row in rows]

    def update_worker(
        self,
        worker_id: str,
        *,
        session_id: str | None = _UNSET,
        role_key: str | None = None,
        agent_profile_id: str | None = None,
        kind: str | None = None,
    ) -> Worker | None:
        if kind is not None and kind not in _WORKER_KINDS:
            raise ValueError(f"unknown worker kind: {kind!r}")
        with self._session() as session:
            row = session.get(SqlWorker, (current_workspace_id(), worker_id))
            if row is None:
                return None
            if session_id is not _UNSET:
                row.session_id = session_id
            if role_key is not None:
                row.role_key = role_key
            if agent_profile_id is not None:
                row.agent_profile_id = agent_profile_id
            if kind is not None:
                row.kind = kind
            row.updated_at = now_epoch()
            session.flush()
            return _worker_to_entity(row)
