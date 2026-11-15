"""Migration ``70752d748475`` adds the watch items table.

At head a watcher tracks an item once per source and source key, and the
status check refuses unknown codes. The downgrade drops the table.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_WATCHER_ID = uuid.uuid4().bytes


def _item(engine: sa.Engine, source_key: str, status: int = 1) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO watch_items (id, watcher_id, source, source_key, title, offsets, "
                "status, created_at, checked_at) "
                "VALUES (:id, :watcher_id, 'github', :source_key, '', '{}', :status, 0, 0)"
            ),
            {
                "id": uuid.uuid4().bytes,
                "watcher_id": _WATCHER_ID,
                "source_key": source_key,
                "status": status,
            },
        )


def test_a_watcher_tracks_an_item_once_until_the_downgrade(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "70752d748475")
        _item(engine, "https://example.com/pr/1")
        with pytest.raises(sa.exc.IntegrityError):
            _item(engine, "https://example.com/pr/1")
        with pytest.raises(sa.exc.IntegrityError):
            _item(engine, "https://example.com/pr/2", status=3)

        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "57e5a00c4b94")

        assert "watch_items" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
        clear_engine_cache()
