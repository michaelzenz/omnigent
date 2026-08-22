"""SQLAlchemy-backed Worker Provider store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from omnigent.db.db_models import SqlWorkerProvider, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker, now_epoch
from omnigent.entities.worker_provider import WorkerProvider
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.worker_provider_store import WorkerProviderStore

_EDITABLE_FIELDS = frozenset({"name", "description", "configuration"})


def _to_entity(row: SqlWorkerProvider) -> WorkerProvider:
    return WorkerProvider(
        id=row.id,
        name=row.name,
        kind=row.kind,
        configuration=row.configuration,
        description=row.description,
        built_in=row.built_in,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyWorkerProviderStore(WorkerProviderStore):
    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.worker_provider_store",
        )

    def get(self, provider_id: str) -> WorkerProvider | None:
        with self._session("select_worker_provider") as session:
            row = session.get(SqlWorkerProvider, (current_workspace_id(), provider_id))
            return _to_entity(row) if row is not None else None

    def list(self) -> list[WorkerProvider]:
        with self._session("list_worker_providers") as session:
            rows = session.execute(
                select(SqlWorkerProvider)
                .where(SqlWorkerProvider.workspace_id == current_workspace_id())
                .order_by(SqlWorkerProvider.created_at.asc(), SqlWorkerProvider.id.asc())
            ).scalars()
            return [_to_entity(row) for row in rows]

    def create(
        self,
        provider_id: str,
        name: str,
        kind: str,
        configuration: str,
        *,
        description: str | None = None,
        built_in: bool = False,
    ) -> WorkerProvider:
        if kind not in {"internal", "external"}:
            raise OmnigentError("Unknown worker provider kind", code=ErrorCode.INVALID_INPUT)
        with self._session("create_worker_provider") as session:
            row = SqlWorkerProvider(
                id=provider_id,
                name=name,
                description=description,
                kind=kind,
                configuration=configuration,
                built_in=built_in,
                created_at=now_epoch(),
            )
            session.add(row)
            session.flush()
            return _to_entity(row)

    def update(self, provider_id: str, **fields: Any) -> WorkerProvider | None:
        unexpected = fields.keys() - _EDITABLE_FIELDS
        if unexpected:
            raise TypeError(f"Unexpected worker provider fields: {sorted(unexpected)!r}")
        with self._session("update_worker_provider") as session:
            row = session.get(SqlWorkerProvider, (current_workspace_id(), provider_id))
            if row is None:
                return None
            for field, value in fields.items():
                setattr(row, field, value)
            if fields:
                row.updated_at = now_epoch()
            session.flush()
            return _to_entity(row)

    def delete(self, provider_id: str) -> bool:
        with self._session("delete_worker_provider") as session:
            row = session.get(SqlWorkerProvider, (current_workspace_id(), provider_id))
            if row is None:
                return False
            if row.built_in:
                raise OmnigentError(
                    "The default worker provider cannot be deleted",
                    code=ErrorCode.CONFLICT,
                )
            session.delete(row)
            session.flush()
            return True
