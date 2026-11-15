"""Migration ``d7fac68e1cf2`` starts the GlobalHub scan indexes with the state.

The dispatcher and the packagers scan every workspace by state, so at head both
indexes start with ``state``. The downgrade restores the queue index that starts
with the workspace and drops the event index.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache, get_or_create_engine


def _indexes(engine: sa.Engine, table: str) -> dict[str, list[str]]:
    return {
        index["name"]: index["column_names"] for index in sa.inspect(engine).get_indexes(table)
    }


def test_the_scan_indexes_start_with_the_state_at_head(tmp_path: Path) -> None:
    engine = get_or_create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        assert _indexes(engine, "agent_queue_items")["ix_agent_queue_items_state_seq"] == [
            "state",
            "seq",
            "workspace_id",
        ]
        assert _indexes(engine, "task_events")["ix_task_events_state"] == [
            "state",
            "created_at",
            "workspace_id",
            "id",
        ]
    finally:
        clear_engine_cache()


def test_downgrade_restores_the_queue_index_that_starts_with_the_workspace(
    tmp_path: Path,
) -> None:
    uri = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "d7fac68e1cf2")
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "4e93b9d0e7bd")
        assert _indexes(engine, "agent_queue_items")["ix_agent_queue_items_state_seq"] == [
            "workspace_id",
            "state",
            "seq",
        ]
        assert "ix_task_events_state" not in _indexes(engine, "task_events")
    finally:
        engine.dispose()
        clear_engine_cache()
