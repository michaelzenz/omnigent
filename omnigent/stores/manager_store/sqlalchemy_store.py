"""SQLAlchemy-backed first-class manager store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, select

from omnigent.db.db_models import SqlManager, current_workspace_id
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
)
from omnigent.entities import Manager
from omnigent.stores.manager_store import ManagerStore

_UNSET: Any = object()


def _normalized_owner(owner_user_id: str | None) -> str:
    return owner_user_id or "__anonymous__"


def _to_entity(row: SqlManager) -> Manager:
    return Manager(
        conversation_id=row.conversation_id,
        owner_user_id=row.owner_user_id,
        role_key=row.role_key,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyManagerStore(ManagerStore):
    """SQLAlchemy-backed implementation of :class:`ManagerStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.manager_store",
        )

    def get(self, conversation_id: str) -> Manager | None:
        with self._session("select_manager_by_conversation") as session:
            row = session.get(SqlManager, (current_workspace_id(), conversation_id))
            return _to_entity(row) if row is not None else None

    def list(self, *, owner_user_id: str | None) -> list[Manager]:
        with self._session("list_managers_by_owner") as session:
            stmt = (
                select(SqlManager)
                .where(
                    SqlManager.workspace_id == current_workspace_id(),
                    SqlManager.owner_user_id == _normalized_owner(owner_user_id),
                )
                .order_by(asc(SqlManager.created_at), asc(SqlManager.conversation_id))
            )
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def upsert(
        self,
        conversation_id: str,
        *,
        owner_user_id: str | None,
        role_key: str,
        description: str,
    ) -> Manager:
        owner_user_id = _normalized_owner(owner_user_id)
        with self._session("upsert_manager") as session:
            row = session.get(SqlManager, (current_workspace_id(), conversation_id))
            now = now_epoch()
            if row is None:
                row = SqlManager(
                    conversation_id=conversation_id,
                    owner_user_id=owner_user_id,
                    role_key=role_key,
                    description=description,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.owner_user_id = owner_user_id
                row.role_key = role_key
                row.description = description
                row.updated_at = now
            session.flush()
            return _to_entity(row)

    def update(
        self,
        conversation_id: str,
        *,
        owner_user_id: Any = _UNSET,
        role_key: str | None = None,
        description: str | None = None,
    ) -> Manager | None:
        with self._session("update_manager") as session:
            row = session.get(SqlManager, (current_workspace_id(), conversation_id))
            if row is None:
                return None
            changed = False
            normalized_owner = (
                _normalized_owner(owner_user_id)
                if owner_user_id is not _UNSET
                else _UNSET
            )
            if normalized_owner is not _UNSET and row.owner_user_id != normalized_owner:
                row.owner_user_id = normalized_owner
                changed = True
            if role_key is not None and row.role_key != role_key:
                row.role_key = role_key
                changed = True
            if description is not None and row.description != description:
                row.description = description
                changed = True
            if changed:
                row.updated_at = now_epoch()
            session.flush()
            return _to_entity(row)
