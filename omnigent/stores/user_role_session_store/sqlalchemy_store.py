"""SQLAlchemy-backed per-user role session store."""

from __future__ import annotations

from sqlalchemy import select

from omnigent.db.db_models import SqlUserRoleSession, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities.task_role_profile import UserRoleSession
from omnigent.stores.user_role_session_store import UserRoleSessionStore


def _to_entity(row: SqlUserRoleSession) -> UserRoleSession:
    return UserRoleSession(
        user_id=row.user_id,
        role=row.role,
        conversation_id=row.conversation_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyUserRoleSessionStore(UserRoleSessionStore):
    """SQLAlchemy-backed implementation of :class:`UserRoleSessionStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)

    def get(self, user_id: str, role: str) -> UserRoleSession | None:
        with self._session() as session:
            row = session.get(SqlUserRoleSession, (current_workspace_id(), user_id, role))
            if row is None:
                return None
            return _to_entity(row)

    def list_for_user(self, user_id: str) -> list[UserRoleSession]:
        with self._session() as session:
            stmt = (
                select(SqlUserRoleSession)
                .where(SqlUserRoleSession.workspace_id == current_workspace_id())
                .where(SqlUserRoleSession.user_id == user_id)
                .order_by(SqlUserRoleSession.role.asc())
            )
            rows = session.execute(stmt).scalars().all()
            return [_to_entity(row) for row in rows]

    def set_conversation(
        self,
        user_id: str,
        role: str,
        conversation_id: str | None,
    ) -> UserRoleSession:
        with self._session() as session:
            row = session.get(SqlUserRoleSession, (current_workspace_id(), user_id, role))
            now = now_epoch()
            if row is None:
                row = SqlUserRoleSession(
                    user_id=user_id,
                    role=role,
                    conversation_id=conversation_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.conversation_id = conversation_id
                row.updated_at = now
            session.flush()
            return _to_entity(row)

    def delete(self, user_id: str, role: str) -> bool:
        with self._session() as session:
            row = session.get(SqlUserRoleSession, (current_workspace_id(), user_id, role))
            if row is None:
                return False
            session.delete(row)
            session.flush()
            return True
