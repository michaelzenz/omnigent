"""SQLAlchemy-backed task asset store."""

from __future__ import annotations

from sqlalchemy import asc, func, select

from omnigent.db.db_models import SqlTaskAsset, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import TaskAsset
from omnigent.stores.task_asset_store import TaskAssetStore


def _asset_to_entity(row: SqlTaskAsset) -> TaskAsset:
    return TaskAsset(
        id=row.id,
        task_id=row.task_id,
        kind=row.kind,
        title=row.title,
        url=row.url,
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
        title: str,
        url: str | None = None,
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
                title=title,
                url=url,
                created_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
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

    def delete_asset(self, task_id: str, asset_id: int) -> bool:
        with self._session() as session:
            row = session.get(SqlTaskAsset, (current_workspace_id(), asset_id))
            if row is None or row.task_id != task_id:
                return False
            session.delete(row)
            session.flush()
            return True
