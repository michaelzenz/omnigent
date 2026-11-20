"""Tests for the migration that adds the staged changes of system notices (b7d41c2e9f06).

Upgrading gives the existing notices no changes; downgrading drops the column.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_PREVIOUS = "4250a650a381"
_REVISION = "b7d41c2e9f06"
_NOTICE_ID = bytes.fromhex("0123456789abcdef0123456789abcdef")


def _migrate(uri: str, engine: sa.Engine, step: str, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        getattr(command, step)(config, revision)


def _columns(engine: sa.Engine) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns("system_notices")}


@pytest.fixture
def database(tmp_path: Path) -> Iterator[tuple[str, sa.Engine]]:
    uri = f"sqlite:///{tmp_path / 'notices.db'}"
    engine = sa.create_engine(uri)
    try:
        yield uri, engine
    finally:
        engine.dispose()
        clear_engine_cache()


def test_upgrade_gives_no_existing_notice_changes_and_downgrade_drops_them(
    database: tuple[str, sa.Engine],
) -> None:
    uri, engine = database
    _migrate(uri, engine, "upgrade", _PREVIOUS)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO system_notices (workspace_id, id, title, created_at) "
                "VALUES (0, :id, 'Stale session cleanup', 1)"
            ),
            {"id": _NOTICE_ID},
        )

    _migrate(uri, engine, "upgrade", _REVISION)

    with engine.connect() as conn:
        changes = conn.execute(sa.text("SELECT changes FROM system_notices")).scalar_one()
    assert changes is None

    _migrate(uri, engine, "downgrade", _PREVIOUS)

    assert "changes" not in _columns(engine)
