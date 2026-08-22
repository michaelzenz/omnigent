from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_PREVIOUS_REVISION = "o3c4d5e6f7a8"
_EXPAND_REVISION = "p4d5e6f7a8b9"
_CONTRACT_REVISION = "q5e6f7a8b9c0"


def _migrate(uri: str, engine: sa.Engine, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_prompt_profile_separation_migration_changes_schema_without_backfill(
    tmp_path: Path,
) -> None:
    uri = f"sqlite:///{tmp_path / 'prompt-profiles.db'}"
    engine = sa.create_engine(uri)
    _migrate(uri, engine, _PREVIOUS_REVISION)

    _migrate(uri, engine, _EXPAND_REVISION)
    inspector = sa.inspect(engine)
    assert "prompt_profiles" in inspector.get_table_names()
    assert "auto_select_enabled" in {column["name"] for column in inspector.get_columns("agents")}
    conversation_columns = {column["name"] for column in inspector.get_columns("conversations")}
    assert {"prompt_profile_mode", "prompt_profile_id"} <= conversation_columns

    prompt_profiles = sa.Table("prompt_profiles", sa.MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        assert connection.execute(sa.select(prompt_profiles)).all() == []

    _migrate(uri, engine, _CONTRACT_REVISION)
    assert "auto_select_enabled" not in {
        column["name"] for column in sa.inspect(engine).get_columns("agents")
    }

    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, _PREVIOUS_REVISION)
    downgraded = sa.inspect(engine)
    assert "prompt_profiles" not in downgraded.get_table_names()
    assert "auto_select_enabled" in {column["name"] for column in downgraded.get_columns("agents")}
    engine.dispose()
    clear_engine_cache()
