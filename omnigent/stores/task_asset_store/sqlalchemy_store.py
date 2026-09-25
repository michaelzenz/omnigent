"""SQLAlchemy-backed task asset store."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import asc, delete, func, select

from omnigent.db.db_models import SqlTaskAsset, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import TaskAsset
from omnigent.stores.task_asset_store import TaskAssetStore


def _asset_to_entity(row: SqlTaskAsset) -> TaskAsset:
    return TaskAsset(
        id=row.id,
        task_id=row.task_id,
        kind=row.kind,
        category=row.category,
        title=row.title,
        url=row.url,
        source_worker_id=row.source_worker_id,
        created_at=row.created_at,
    )


class SqlAlchemyTaskAssetStore(TaskAssetStore):
    """SQLAlchemy-backed implementation of :class:`TaskAssetStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)

    def create_asset(
        self,
        task_id: str,
        *,
        kind: str,
        category: str = "other",
        title: str,
        url: str | None = None,
        source_worker_id: str | None = None,
    ) -> TaskAsset:
        now = now_epoch()
        workspace_id = current_workspace_id()
        with self._session() as session:
            next_id = session.scalar(
                select(func.coalesce(func.max(SqlTaskAsset.id), 0) + 1).where(
                    SqlTaskAsset.workspace_id == workspace_id,
                ),
            )
            assert next_id is not None
            row = SqlTaskAsset(
                workspace_id=workspace_id,
                id=next_id,
                task_id=task_id,
                kind=kind,
                category=category,
                title=title,
                url=url,
                source_worker_id=source_worker_id,
                created_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _asset_to_entity(row)

    def upsert_asset(
        self,
        task_id: str,
        *,
        kind: str,
        category: str = "other",
        title: str,
        url: str,
        source_worker_id: str | None = None,
    ) -> TaskAsset:
        workspace_id = current_workspace_id()
        with self._session() as session:
            next_id = session.scalar(
                select(func.coalesce(func.max(SqlTaskAsset.id), 0) + 1).where(
                    SqlTaskAsset.workspace_id == workspace_id,
                ),
            )
            assert next_id is not None
            dialect = cast(Any, session.bind).dialect.name
            values: dict[str, Any] = {
                "workspace_id": workspace_id,
                "id": next_id,
                "task_id": task_id,
                "kind": kind,
                "category": category,
                "title": title,
                "url": url,
                "created_at": now_epoch(),
            }
            if source_worker_id is not None:
                values["source_worker_id"] = source_worker_id
            # A provided provenance re-points the chip to the latest harvester;
            # omitted provenance leaves any existing value untouched (a manual
            # re-post of the same URL must not clear it).
            conflict_update = {"kind": kind, "category": category, "title": title}
            if source_worker_id is not None:
                conflict_update["source_worker_id"] = source_worker_id
            if dialect == "mysql":
                from sqlalchemy.dialects.mysql import insert as mysql_insert

                stmt = (
                    mysql_insert(SqlTaskAsset)
                    .values(**values)
                    .on_duplicate_key_update(**conflict_update)
                )
            else:
                if dialect == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert as pg_insert

                    insert_cls = pg_insert
                else:
                    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                    insert_cls = sqlite_insert
                stmt = (
                    insert_cls(SqlTaskAsset)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["workspace_id", "task_id", "url"],
                        set_=conflict_update,
                    )
                )
            session.execute(stmt)
            session.commit()
        row = session.scalars(
            select(SqlTaskAsset)
            .where(SqlTaskAsset.workspace_id == workspace_id)
            .where(SqlTaskAsset.task_id == task_id)
            .where(SqlTaskAsset.url == url)
            .limit(1)
        ).first()
        assert row is not None
        return _asset_to_entity(row)

    def list_assets_for_task(self, task_id: str) -> list[TaskAsset]:
        with self._session() as session:
            stmt = (
                select(SqlTaskAsset)
                .where(SqlTaskAsset.workspace_id == current_workspace_id())
                .where(SqlTaskAsset.task_id == task_id)
                .order_by(asc(SqlTaskAsset.id))
            )
            rows = session.scalars(stmt).all()
            return [_asset_to_entity(row) for row in rows]

    def list_assets_for_tasks(self, task_ids: list[str]) -> list[TaskAsset]:
        if not task_ids:
            return []
        with self._session() as session:
            stmt = (
                select(SqlTaskAsset)
                .where(SqlTaskAsset.workspace_id == current_workspace_id())
                .where(SqlTaskAsset.task_id.in_(task_ids))
                .order_by(asc(SqlTaskAsset.task_id), asc(SqlTaskAsset.id))
            )
            rows = session.scalars(stmt).all()
            return [_asset_to_entity(row) for row in rows]

    def delete_asset(self, task_id: str, asset_id: int) -> bool:
        with self._session() as session:
            row = session.get(SqlTaskAsset, (current_workspace_id(), asset_id))
            if row is None or row.task_id != task_id:
                return False
            session.delete(row)
            session.flush()
            return True

    def delete_assets_for_task(self, task_id: str) -> int:
        with self._session() as session:
            result = session.execute(
                delete(SqlTaskAsset).where(
                    SqlTaskAsset.workspace_id == current_workspace_id(),
                    SqlTaskAsset.task_id == task_id,
                )
            )
            session.flush()
            return cast(Any, result).rowcount or 0
