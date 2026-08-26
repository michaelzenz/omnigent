"""SQLAlchemy-backed deployment tool preferences store."""

from __future__ import annotations

import json

from sqlalchemy import select

from omnigent.db.db_models import SqlToolPreferences
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
)
from omnigent.stores.tool_preferences_store import ToolPreferences, ToolPreferencesStore


def _decode(row: SqlToolPreferences) -> ToolPreferences:
    raw = json.loads(row.disabled_tools)
    if not isinstance(raw, list):
        raise ValueError("tool_preferences.disabled_tools must be a JSON array")
    return ToolPreferences(disabled_tools=frozenset(raw))


class SqlAlchemyToolPreferencesStore(ToolPreferencesStore):
    """Persist one global tool-preferences row for the deployment."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.tool_preferences_store",
        )

    def get(self) -> ToolPreferences:
        with self._session("get") as session:
            row = session.get(SqlToolPreferences, 1)
            if row is None:
                return ToolPreferences(disabled_tools=frozenset())
            return _decode(row)

    def update(
        self,
        *,
        disabled_tools: list[str],
        updated_by: str | None = None,
    ) -> ToolPreferences:
        import time

        with self._session("update") as session:
            row = session.execute(
                select(SqlToolPreferences).where(SqlToolPreferences.id == 1).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                row = SqlToolPreferences(
                    id=1,
                    disabled_tools=json.dumps(sorted(disabled_tools)),
                    updated_at=int(time.time()),
                    updated_by=updated_by,
                )
                session.add(row)
            else:
                row.disabled_tools = json.dumps(sorted(disabled_tools))
                row.updated_at = int(time.time())
                row.updated_by = updated_by
            session.flush()
            return _decode(row)
