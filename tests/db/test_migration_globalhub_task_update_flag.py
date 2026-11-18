"""Tests for the migration that adds the update flag of GlobalHub tasks (5ba8fed0798c).

Upgrading gives the existing tasks no flag; downgrading drops the column.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_PREVIOUS = "8e2b6d4f1a93"
_REVISION = "5ba8fed0798c"
_TASK_ID = bytes.fromhex("0123456789abcdef0123456789abcdef")


def _migrate(uri: str, engine: sa.Engine, step: str, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        getattr(command, step)(config, revision)


def _columns(engine: sa.Engine) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns("tasks")}


@pytest.fixture
def database(tmp_path: Path) -> Iterator[tuple[str, sa.Engine]]:
    uri = f"sqlite:///{tmp_path / 'tasks.db'}"
    engine = sa.create_engine(uri)
    try:
        yield uri, engine
    finally:
        engine.dispose()
        clear_engine_cache()


def test_upgrade_flags_no_existing_task_and_downgrade_drops_the_flag(
    database: tuple[str, sa.Engine],
) -> None:
    uri, engine = database
    _migrate(uri, engine, "upgrade", _PREVIOUS)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tasks (workspace_id, id, title, state, priority, queue_rank, "
                "created_at) VALUES (0, :id, 'S3 retries', 1, 2, 0, 1)"
            ),
            {"id": _TASK_ID},
        )

    _migrate(uri, engine, "upgrade", _REVISION)

    with engine.connect() as conn:
        flag = conn.execute(sa.text("SELECT has_update FROM tasks")).scalar_one()
    assert bool(flag) is False

    _migrate(uri, engine, "downgrade", _PREVIOUS)

    assert "has_update" not in _columns(engine)
