from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_PREVIOUS_REVISION = "m1b2c3d4e5f6"
_REVISION = "n2b3c4d5e6f7"


def _migrate(uri: str, engine: sa.Engine, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_smart_routing_model_settings_migration_round_trip(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'smart-routing.db'}"
    engine = sa.create_engine(uri)
    _migrate(uri, engine, _PREVIOUS_REVISION)
    _migrate(uri, engine, _REVISION)

    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT smart_routing_decision_model, smart_routing_prompt, "
                "smart_routing_cadence FROM model_settings WHERE id = 1"
            )
        ).one()
    assert row == ("databricks-gpt-5-6-luna", "", "per_turn")

    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, _PREVIOUS_REVISION)
    columns = {column["name"] for column in sa.inspect(engine).get_columns("model_settings")}
    assert "smart_routing_decision_model" not in columns
    assert "smart_routing_prompt" not in columns
    assert "smart_routing_cadence" not in columns
    engine.dispose()
    clear_engine_cache()
