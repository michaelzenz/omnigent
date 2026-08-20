from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache


def test_workload_categories_migration_defaults_existing_settings(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'workload-categories.db'}"
    engine = sa.create_engine(uri)
    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "t1b2c3d4e5f6")
        command.upgrade(config, "u2c3d4e5f6g7")
        categories = connection.execute(
            sa.text("SELECT workload_custom_categories FROM model_settings WHERE id = 1")
        ).scalar_one()
        assert categories == "[]"
        command.downgrade(config, "t1b2c3d4e5f6")
        assert "workload_custom_categories" not in {
            column["name"] for column in sa.inspect(connection).get_columns("model_settings")
        }
    engine.dispose()
    clear_engine_cache()
