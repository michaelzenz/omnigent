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
        target_id=row.target_id,
        state=row.state,
        needs_response=row.needs_response,
        provider_name=row.provider_name,
        provider_configuration=row.provider_configuration,
        failure_reason=row.failure_reason,
        last_observed_at=row.last_observed_at,
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
        target_id: str | None = None,
        state: str = "uninitialized",
        needs_response: bool = False,
        provider_name: str | None = None,
        provider_configuration: str | None = None,
    ) -> Worker:
        if kind not in _WORKER_KINDS:
            raise ValueError(f"unknown worker kind: {kind!r}")
        row = SqlWorker(
            id=worker_id,
            task_id=task_id,
            kind=kind,
            target_id=target_id,
            state=state,
            needs_response=needs_response,
            provider_name=provider_name,
            provider_configuration=provider_configuration,
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

    def get_by_target_id(self, target_id: str) -> Worker | None:
        with self._session() as session:
            stmt = (
                select(SqlWorker)
                .where(SqlWorker.workspace_id == current_workspace_id())
                .where(SqlWorker.target_id == target_id)
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
        kind: str | None = None,
        target_id: str | None = _UNSET,
        state: str | None = None,
        needs_response: bool | None = None,
        failure_reason: str | None = _UNSET,
        last_observed_at: int | None = _UNSET,
    ) -> Worker | None:
        if kind is not None and kind not in _WORKER_KINDS:
            raise ValueError(f"unknown worker kind: {kind!r}")
        with self._session() as session:
            row = session.get(SqlWorker, (current_workspace_id(), worker_id))
            if row is None:
                return None
            if kind is not None:
                row.kind = kind
            if target_id is not _UNSET:
                row.target_id = target_id
            if state is not None:
                row.state = state
            if needs_response is not None:
                row.needs_response = needs_response
            if failure_reason is not _UNSET:
                row.failure_reason = failure_reason
            if last_observed_at is not _UNSET:
                row.last_observed_at = last_observed_at
            row.updated_at = now_epoch()
            session.flush()
            return _worker_to_entity(row)
