from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache


def test_memory_provider_migration_defaults_existing_rows(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'memory-provider.db'}"
    engine = sa.create_engine(uri)
    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "s7g8h9i0j1k2")
        connection.execute(
            sa.text(
                "INSERT INTO memory_settings "
                "(workspace_id, user_id, max_tokens, updated_at) "
                "VALUES (0, 'alice', 20000, 1)"
            )
        )
        command.upgrade(config, "t1b2c3d4e5f6")
        provider = connection.execute(
            sa.text(
                "SELECT provider FROM memory_settings WHERE workspace_id = 0 AND user_id = 'alice'"
            )
        ).scalar_one()
        assert provider == "omniharness"
        command.downgrade(config, "s7g8h9i0j1k2")
        assert "provider" not in {
            column["name"] for column in sa.inspect(connection).get_columns("memory_settings")
        }
    engine.dispose()
    clear_engine_cache()
