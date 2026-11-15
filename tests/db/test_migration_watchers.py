"""Migration ``57e5a00c4b94`` adds the watchers table.

At head the table and its two indexes exist. The downgrade drops the table
and deletes the watchers' schedules, but keeps the other roles' schedules.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache


def _schedule(engine: sa.Engine, role_key: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO role_schedules (id, role_key, rrule, timezone, created_at) "
                "VALUES (:id, :role_key, 'FREQ=HOURLY', 'UTC', 0)"
            ),
            {"id": uuid.uuid4().bytes, "role_key": role_key},
        )


def test_the_downgrade_drops_watchers_and_their_schedules(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "57e5a00c4b94")
        indexes = {
            index["name"]: index["column_names"]
            for index in sa.inspect(engine).get_indexes("watchers")
        }
        _schedule(engine, f"watcher:{uuid.uuid4().hex}")
        _schedule(engine, "custodian")

        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "62d77984d129")

        assert indexes == {
            "ix_watchers_conversation": ["workspace_id", "conversation_id"],
            "ix_watchers_owner": ["workspace_id", "owner_user_id", "created_at", "id"],
        }
        assert "watchers" not in sa.inspect(engine).get_table_names()
        with engine.connect() as conn:
            role_keys = conn.execute(sa.text("SELECT role_key FROM role_schedules")).scalars()
            assert list(role_keys) == ["custodian"]
    finally:
        engine.dispose()
        clear_engine_cache()
