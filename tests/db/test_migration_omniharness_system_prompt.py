from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache


def test_omniharness_system_prompt_migration_defaults_existing_settings(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'omniharness-system-prompt.db'}"
    engine = sa.create_engine(uri)
    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "u2c3d4e5f6g7")
        command.upgrade(config, "v3d4e5f6g7h8")
        prompt = connection.execute(
            sa.text("SELECT omniharness_system_prompt FROM model_settings WHERE id = 1")
        ).scalar_one()
        assert prompt == ""
        command.downgrade(config, "u2c3d4e5f6g7")
        assert "omniharness_system_prompt" not in {
            column["name"] for column in sa.inspect(connection).get_columns("model_settings")
        }
    engine.dispose()
    clear_engine_cache()
