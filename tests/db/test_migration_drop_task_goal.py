"""Tests for the migration that drops ``goal`` from GlobalHub tasks (9b3f6d2a8c41).

Upgrading drops the column and keeps every task's other fields; downgrading
adds the column back with an empty goal.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.db_models import SqlTask
from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_PREVIOUS = "7c4e2b9a1d6f"
_REVISION = "9b3f6d2a8c41"
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


def test_the_model_has_no_goal() -> None:
    assert "goal" not in SqlTask.__table__.c


def test_upgrade_drops_goal_and_keeps_tasks(database: tuple[str, sa.Engine]) -> None:
    uri, engine = database
    _migrate(uri, engine, "upgrade", _PREVIOUS)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tasks (workspace_id, id, title, goal, state, priority, "
                "queue_rank, created_at) VALUES (0, :id, 'S3 retries', 'Merge it', 1, 2, 0, 1)"
            ),
            {"id": _TASK_ID},
        )

    _migrate(uri, engine, "upgrade", _REVISION)

    assert "goal" not in _columns(engine)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT title FROM tasks")).scalar_one() == "S3 retries"

    _migrate(uri, engine, "downgrade", _PREVIOUS)

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT title, goal FROM tasks")).one() == ("S3 retries", "")
