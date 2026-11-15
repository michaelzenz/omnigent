"""Migration ``62d77984d129`` adds the custodian sweeps and their queue item kind.

At head the queue accepts the daily cleanup kind (4) and the due index starts
with ``next_run_at``. The downgrade deletes the cleanup items, restores the old
kind check and drops the table.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_CLEANUP_KIND = 4


def _queue_item(engine: sa.Engine, kind: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO agent_queue_items (id, target_session_id, kind, seq, created_at) "
                "VALUES (:id, :target, :kind, 1, 0)"
            ),
            {"id": uuid.uuid4().bytes, "target": uuid.uuid4().bytes, "kind": kind},
        )


def test_the_queue_takes_the_cleanup_kind_until_the_downgrade_drops_it(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "62d77984d129")
        _queue_item(engine, _CLEANUP_KIND)
        due_index = {
            index["name"]: index["column_names"]
            for index in sa.inspect(engine).get_indexes("custodian_sweeps")
        }["ix_custodian_sweeps_due"]

        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "d7fac68e1cf2")

        assert due_index == ["next_run_at", "workspace_id", "id"]
        assert "custodian_sweeps" not in sa.inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM agent_queue_items")).scalar() == 0
        with pytest.raises(sa.exc.IntegrityError):
            _queue_item(engine, _CLEANUP_KIND)
    finally:
        engine.dispose()
        clear_engine_cache()
