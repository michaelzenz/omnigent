"""SQLAlchemy-backed prompt profile store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from omnigent.db.converters import sql_prompt_profile_to_entity
from omnigent.db.db_models import SqlPromptProfile, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker, now_epoch
from omnigent.entities import PromptProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.prompt_profile_store import PromptProfileStore

_EDITABLE_FIELDS = frozenset({"name", "description", "instructions", "enabled", "visible"})


class SqlAlchemyPromptProfileStore(PromptProfileStore):
    """Persist plain-text profiles in the Omnigent operational database."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.prompt_profile_store",
        )

    @staticmethod
    def _name_taken(session: Any, name: str, *, exclude_id: str | None = None) -> bool:
        stmt = select(SqlPromptProfile.id).where(
            SqlPromptProfile.workspace_id == current_workspace_id(),
            SqlPromptProfile.name == name,
        )
        if exclude_id is not None:
            stmt = stmt.where(SqlPromptProfile.id != exclude_id)
        return session.execute(stmt.limit(1)).first() is not None

    def create(
        self,
        profile_id: str,
        name: str,
        instructions: str,
        *,
        description: str | None = None,
        enabled: bool = True,
        visible: bool = True,
    ) -> PromptProfile:
        with self._session("create_prompt_profile") as session:
            if self._name_taken(session, name):
                raise OmnigentError(
                    f"Prompt profile name already exists: {name!r}",
                    code=ErrorCode.ALREADY_EXISTS,
                )
            row = SqlPromptProfile(
                id=profile_id,
                name=name,
                description=description,
                instructions=instructions,
                enabled=enabled,
                visible=visible,
                created_at=now_epoch(),
                updated_at=None,
            )
            session.add(row)
            return sql_prompt_profile_to_entity(row)

    def get(self, profile_id: str) -> PromptProfile | None:
        with self._session("select_prompt_profile_by_id") as session:
            row = session.get(SqlPromptProfile, (current_workspace_id(), profile_id))
            return sql_prompt_profile_to_entity(row) if row is not None else None

    def list(
        self,
        *,
        enabled_only: bool = False,
        visible_only: bool = True,
    ) -> list[PromptProfile]:
        with self._session("list_active_prompt_profiles") as session:
            stmt = select(SqlPromptProfile).where(
                SqlPromptProfile.workspace_id == current_workspace_id(),
            )
            if enabled_only:
                stmt = stmt.where(SqlPromptProfile.enabled.is_(True))
            if visible_only:
                stmt = stmt.where(SqlPromptProfile.visible.is_(True))
            rows = session.execute(
                stmt.order_by(SqlPromptProfile.created_at.asc(), SqlPromptProfile.id.asc())
            ).scalars()
            return [sql_prompt_profile_to_entity(row) for row in rows]

    def update(self, profile_id: str, **fields: Any) -> PromptProfile | None:
        unexpected = fields.keys() - _EDITABLE_FIELDS
        if unexpected:
            raise TypeError(f"Unexpected prompt profile fields: {sorted(unexpected)!r}")
        with self._session("update_prompt_profile") as session:
            row = session.get(SqlPromptProfile, (current_workspace_id(), profile_id))
            if row is None:
                return None
            name = fields.get("name")
            if (
                name is not None
                and name != row.name
                and self._name_taken(session, name, exclude_id=profile_id)
            ):
                raise OmnigentError(
                    f"Prompt profile name already exists: {name!r}",
                    code=ErrorCode.ALREADY_EXISTS,
                )
            for field, value in fields.items():
                setattr(row, field, value)
            if fields:
                row.updated_at = now_epoch()
            return sql_prompt_profile_to_entity(row)

    def delete(self, profile_id: str) -> bool:
        with self._session("delete_prompt_profile") as session:
            row = session.get(SqlPromptProfile, (current_workspace_id(), profile_id))
            if row is None:
                return False
            session.delete(row)
            return True
