"""SQLAlchemy-backed GlobalHub task asset store."""

from __future__ import annotations

import builtins
import json
from collections.abc import Collection

from sqlalchemy import ColumnElement, and_, asc, select
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlTaskAsset, current_workspace_id
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import TaskAsset
from omnigent.stores.task_asset_store import TaskAssetStore
from omnigent.util.uuid import normalize_uuid


def _to_entity(row: SqlTaskAsset) -> TaskAsset:
    return TaskAsset(
        id=row.id,
        owner_user_id=row.owner_user_id,
        task_id=row.task_id,
        category=row.category,
        title=row.title,
        url=row.url,
        created_at=row.created_at,
        related_session_ids=json.loads(row.related_session_ids),
        updated_at=row.updated_at,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlTaskAsset.workspace_id == current_workspace_id(),
        SqlTaskAsset.owner_user_id == owner_user_id,
    )


class SqlAlchemyTaskAssetStore(TaskAssetStore):
    """
    SQLAlchemy-backed implementation of :class:`TaskAssetStore`.

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
            query_name_prefix="omnigent.task_asset_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.task_asset_store",
            immediate=True,
        )

    def upsert(
        self,
        asset_id: str,
        *,
        owner_user_id: str | None,
        task_id: str,
        url: str,
        title: str,
        category: str = "other",
        add_related_session_ids: Collection[str] = (),
    ) -> TaskAsset:
        """Attach a URL to the owner's task, or update the asset the task has for it."""
        now = now_epoch()
        added = [
            normalize_uuid(session_id) or session_id for session_id in add_related_session_ids
        ]

        def write(session: Session) -> TaskAsset:
            require_active_account(session, owner_user_id)
            stmt = select(SqlTaskAsset).where(
                _owned(owner_user_id), SqlTaskAsset.task_id == task_id, SqlTaskAsset.url == url
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                row = SqlTaskAsset(
                    id=asset_id,
                    owner_user_id=owner_user_id,
                    task_id=task_id,
                    category=category,
                    title=title,
                    url=url,
                    related_session_ids=json.dumps(list(dict.fromkeys(added))),
                    created_at=now,
                    updated_at=None,
                )
                session.add(row)
                return _to_entity(row)
            related = json.loads(row.related_session_ids)
            merged = list(dict.fromkeys([*related, *added]))
            if (row.title, row.category, related) != (title, category, merged):
                row.title = title
                row.category = category
                row.related_session_ids = json.dumps(merged)
                row.updated_at = now
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "upsert_task_asset", write)

    def list_for_tasks(
        self, task_ids: Collection[str], *, owner_user_id: str | None
    ) -> builtins.list[TaskAsset]:
        """List the owner's assets on these tasks, by task and then attach order."""
        if not task_ids:
            return []
        stmt = (
            select(SqlTaskAsset)
            .where(_owned(owner_user_id), SqlTaskAsset.task_id.in_(task_ids))
            .order_by(
                asc(SqlTaskAsset.task_id), asc(SqlTaskAsset.created_at), asc(SqlTaskAsset.id)
            )
        )
        with self._session("list_task_assets_for_tasks") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def delete(self, asset_id: str, *, owner_user_id: str | None, task_id: str) -> bool:
        """Detach the owner's asset from ``task_id``; return whether it was there."""

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            row = session.get(SqlTaskAsset, (current_workspace_id(), asset_id))
            if (
                row is None
                or row.owner_user_id != owner_user_id
                or row.task_id != normalize_uuid(task_id)
            ):
                return False
            session.delete(row)
            return True

        return run_write_transaction(self._session_immediate, "delete_task_asset", write)
