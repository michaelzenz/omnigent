"""SQLAlchemy-backed categorized user memory store."""

from __future__ import annotations

import uuid

from sqlalchemy import asc, select

from omnigent.db.db_models import SqlMemoryCategory, SqlMemorySettings, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker, now_epoch
from omnigent.entities.memory import MemoryCategory
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.memory import count_memory_tokens
from omnigent.stores.memory_store import DEFAULT_MEMORY_CATEGORY_NAMES, MemoryStore


def _to_entity(row: SqlMemoryCategory) -> MemoryCategory:
    return MemoryCategory(
        id=row.id,
        name=row.name,
        user_id=row.user_id,
        display_order=row.display_order,
        content=row.content,
        token_count=row.token_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyMemoryStore(MemoryStore):
    """Relational store scoped by ambient workspace and explicit user."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine, query_name_prefix="omnigent.memory_store"
        )

    @staticmethod
    def _owned_stmt(user_id: str | None):
        return select(SqlMemoryCategory).where(
            SqlMemoryCategory.workspace_id == current_workspace_id(),
            SqlMemoryCategory.user_id == user_id,
        )

    def list(self, *, user_id: str | None, seed_defaults: bool = True) -> list[MemoryCategory]:
        with self._session("list_memory_categories") as session:
            rows = (
                session.execute(
                    self._owned_stmt(user_id).order_by(
                        asc(SqlMemoryCategory.display_order), asc(SqlMemoryCategory.id)
                    )
                )
                .scalars()
                .all()
            )
            if not rows and seed_defaults:
                timestamp = now_epoch()
                rows = [
                    SqlMemoryCategory(
                        id=uuid.uuid4().hex,
                        user_id=user_id,
                        name=name,
                        display_order=position,
                        content="",
                        token_count=0,
                        created_at=timestamp,
                        updated_at=None,
                    )
                    for position, name in enumerate(DEFAULT_MEMORY_CATEGORY_NAMES)
                ]
                session.add_all(rows)
                session.flush()
            return [_to_entity(row) for row in rows]

    def create(
        self,
        category_id: str,
        *,
        user_id: str | None,
        name: str,
        content: str = "",
        display_order: int | None = None,
    ) -> MemoryCategory:
        with self._session("create_memory_category") as session:
            if display_order is None:
                rows = session.execute(self._owned_stmt(user_id)).scalars().all()
                display_order = max((row.display_order for row in rows), default=-1) + 1
            row = SqlMemoryCategory(
                id=category_id,
                user_id=user_id,
                name=name,
                display_order=display_order,
                content=content,
                token_count=count_memory_tokens(content),
                created_at=now_epoch(),
                updated_at=None,
            )
            session.add(row)
            session.flush()
            return _to_entity(row)

    def update(
        self,
        category_id: str,
        *,
        user_id: str | None,
        name: str | None = None,
        content: str | None = None,
        display_order: int | None = None,
    ) -> MemoryCategory | None:
        with self._session("update_memory_category") as session:
            row = session.get(SqlMemoryCategory, (current_workspace_id(), category_id))
            if row is None or row.user_id != user_id:
                return None
            changed = False
            if name is not None and name != row.name:
                row.name = name
                changed = True
            if content is not None and content != row.content:
                row.content = content
                row.token_count = count_memory_tokens(content)
                changed = True
            if display_order is not None and display_order != row.display_order:
                row.display_order = display_order
                changed = True
            if changed:
                row.updated_at = now_epoch()
            session.flush()
            return _to_entity(row)

    def delete(self, category_id: str, *, user_id: str | None) -> bool:
        with self._session("delete_memory_category") as session:
            row = session.get(SqlMemoryCategory, (current_workspace_id(), category_id))
            if row is None or row.user_id != user_id:
                return False
            session.delete(row)
            return True

    def reorder(self, category_ids: list[str], *, user_id: str | None) -> list[MemoryCategory]:
        with self._session("reorder_memory_categories") as session:
            rows = session.execute(self._owned_stmt(user_id)).scalars().all()
            by_id = {row.id: row for row in rows}
            if len(category_ids) != len(set(category_ids)) or set(category_ids) != set(by_id):
                raise OmnigentError(
                    "ordered_ids must contain every category exactly once",
                    code=ErrorCode.INVALID_INPUT,
                )
            timestamp = now_epoch()
            for position, category_id in enumerate(category_ids):
                row = by_id[category_id]
                if row.display_order != position:
                    row.display_order = position
                    row.updated_at = timestamp
            session.flush()
            return [_to_entity(by_id[category_id]) for category_id in category_ids]

    def get_max_tokens(self, *, user_id: str | None, default: int) -> int:
        with self._session("get_memory_settings") as session:
            row = session.get(
                SqlMemorySettings,
                (current_workspace_id(), user_id or ""),
            )
            return row.max_tokens if row is not None else default

    def set_max_tokens(self, max_tokens: int, *, user_id: str | None) -> int:
        with self._session("upsert_memory_settings") as session:
            key = (current_workspace_id(), user_id or "")
            row = session.get(SqlMemorySettings, key)
            if row is None:
                row = SqlMemorySettings(
                    workspace_id=key[0],
                    user_id=key[1],
                    max_tokens=max_tokens,
                    updated_at=now_epoch(),
                )
                session.add(row)
            else:
                row.max_tokens = max_tokens
                row.updated_at = now_epoch()
            return row.max_tokens
