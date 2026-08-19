from __future__ import annotations

import hashlib
from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_PREVIOUS_REVISION = "n2b3c4d5e6f7"
_REVISION = "o3c4d5e6f7a8"


def _migrate(uri: str, engine: sa.Engine, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_profile_auto_select_migration_backfills_only_custom_profiles(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'profile-auto-select.db'}"
    engine = sa.create_engine(uri)
    _migrate(uri, engine, _PREVIOUS_REVISION)

    agents = sa.Table("agents", sa.MetaData(), autoload_with=engine)
    builtin_name = "packaged"
    builtin_id = hashlib.sha256(f"builtin:{builtin_name}".encode()).hexdigest()[:32]
    with engine.begin() as connection:
        connection.execute(
            agents.insert(),
            [
                {
                    "id": bytes.fromhex("aa" * 16),
                    "created_at": 1,
                    "name": "enabled-profile",
                    "bundle_location": "profile/enabled",
                    "version": 1,
                    "kind": 1,
                    "is_role": False,
                    "enabled": True,
                    "archived": False,
                },
                {
                    "id": bytes.fromhex("bb" * 16),
                    "created_at": 1,
                    "name": "disabled-profile",
                    "bundle_location": "profile/disabled",
                    "version": 1,
                    "kind": 1,
                    "is_role": False,
                    "enabled": False,
                    "archived": False,
                },
                {
                    "id": bytes.fromhex(builtin_id),
                    "created_at": 1,
                    "name": builtin_name,
                    "bundle_location": "builtin/packaged",
                    "version": 1,
                    "kind": 1,
                    "is_role": False,
                    "enabled": True,
                    "archived": False,
                },
            ],
        )

    _migrate(uri, engine, _REVISION)
    migrated = sa.Table("agents", sa.MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        rows = {
            row.name: row
            for row in connection.execute(
                sa.select(
                    migrated.c.name,
                    migrated.c.enabled,
                    migrated.c.auto_select_enabled,
                )
            )
        }
    assert rows["enabled-profile"].auto_select_enabled is True
    assert rows["disabled-profile"].auto_select_enabled is False
    assert rows["disabled-profile"].enabled is True
    assert rows[builtin_name].auto_select_enabled is None

    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, _PREVIOUS_REVISION)
    columns = {column["name"] for column in sa.inspect(engine).get_columns("agents")}
    assert "auto_select_enabled" not in columns
    engine.dispose()
    clear_engine_cache()
