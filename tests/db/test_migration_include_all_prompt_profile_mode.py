from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_PREVIOUS_REVISION = "zzh2i3j4k5l6"
_INCLUDE_ALL_REVISION = "zzj1k2l3m4n5"


def _migrate(uri: str, revision: str) -> None:
    config = _build_alembic_config(uri)
    with (
        sa.create_engine(uri) as engine,
        engine.begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def _conversation_row(engine: sa.Engine, conv_id: str, mode: str | None) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO conversations (id, root_conversation_id, "
                "created_at, updated_at, prompt_profile_mode) "
                "VALUES (:id, :id, 0, 0, :mode)"
            ),
            {"id": conv_id, "mode": mode},
        )


def test_include_all_prompt_profile_mode_migration_round_trip(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'prompt-profile-include-all.db'}"
    _migrate(uri, _PREVIOUS_REVISION)
    engine = sa.create_engine(uri)

    # The old constraint rejects the new mode before the migration...
    with pytest.raises(sa.exc.IntegrityError):
        _conversation_row(engine, "conv_inc", "include_all")
    engine.dispose()

    # ...accepts it after, and remaps include_all rows on downgrade.
    _migrate(uri, _INCLUDE_ALL_REVISION)
    engine = sa.create_engine(uri)
    _conversation_row(engine, "conv_inc", "include_all")

    config = _build_alembic_config(uri)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, _PREVIOUS_REVISION)
    with engine.connect() as connection:
        remapped = connection.execute(
            sa.text("SELECT prompt_profile_mode FROM conversations WHERE id = 'conv_inc'")
        ).scalar_one()
        assert remapped == "auto_include"
        # The restored old constraint rejects the new mode again.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO conversations (id, root_conversation_id, "
                    "created_at, updated_at, prompt_profile_mode) "
                    "VALUES ('conv_new', 'conv_new', 0, 0, 'include_all')"
                )
            )

    engine.dispose()
    clear_engine_cache()
