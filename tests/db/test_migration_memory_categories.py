from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache


def _migrate(uri: str, engine: sa.Engine, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_memory_categories_migration_round_trip(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'memory.db'}"
    engine = sa.create_engine(uri)
    _migrate(uri, engine, "k0a1b2c3d4e5")
    _migrate(uri, engine, "m1b2c3d4e5f6")

    columns = {
        column["name"] for column in sa.inspect(engine).get_columns("memory_categories")
    }
    assert {
        "workspace_id",
        "id",
        "user_id",
        "name",
        "display_order",
        "content",
        "token_count",
        "created_at",
        "updated_at",
    } == columns
    assert {
        "workspace_id",
        "user_id",
        "max_tokens",
        "updated_at",
    } == {column["name"] for column in sa.inspect(engine).get_columns("memory_settings")}

    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "k0a1b2c3d4e5")
    assert "memory_categories" not in sa.inspect(engine).get_table_names()
    assert "memory_settings" not in sa.inspect(engine).get_table_names()
    engine.dispose()
    clear_engine_cache()
