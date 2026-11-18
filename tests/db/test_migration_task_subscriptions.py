"""Migration ``1306afe778d8`` adds task subscriptions.

At that revision a task follows a key once, and ``watch_items`` has no task
column. The downgrade drops the table; a later revision drops it at head.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_TASK_ID = uuid.uuid4().bytes
_PR = "https://github.com/example-org/repo/pull/12"


def _subscribe(engine: sa.Engine, source_key: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO task_subscriptions (id, task_id, source_key, created_at) "
                "VALUES (:id, :task_id, :source_key, 0)"
            ),
            {"id": uuid.uuid4().bytes, "task_id": _TASK_ID, "source_key": source_key},
        )


def test_a_task_follows_a_key_once_until_the_downgrade(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "1306afe778d8")
        _subscribe(engine, _PR)
        _subscribe(engine, f"{_PR}/files")
        with pytest.raises(sa.exc.IntegrityError):
            _subscribe(engine, _PR)
        watch_item_columns = {
            column["name"] for column in sa.inspect(engine).get_columns("watch_items")
        }

        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "70752d748475")

        assert "task_id" not in watch_item_columns
        assert "task_subscriptions" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
        clear_engine_cache()
