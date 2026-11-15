"""Migration ``62d77984d129`` adds the role schedules and their queue item kind.

At head the queue accepts the scheduled run kind (4), the owner index is
unique and the due index starts with ``next_run_at``. The downgrade deletes the
run items, restores the old kind check and drops the table.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_SCHEDULED_RUN_KIND = 4


def _queue_item(engine: sa.Engine, kind: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO agent_queue_items (id, target_session_id, kind, seq, created_at) "
                "VALUES (:id, :target, :kind, 1, 0)"
            ),
            {"id": uuid.uuid4().bytes, "target": uuid.uuid4().bytes, "kind": kind},
        )


def test_the_queue_takes_the_run_kind_until_the_downgrade_drops_it(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "62d77984d129")
        _queue_item(engine, _SCHEDULED_RUN_KIND)
        indexes = {
            index["name"]: (index["column_names"], bool(index["unique"]))
            for index in sa.inspect(engine).get_indexes("role_schedules")
        }

        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "d7fac68e1cf2")

        assert indexes == {
            "ix_role_schedules_due": (["next_run_at", "workspace_id", "id"], False),
            # An owner has at most one schedule per role.
            "ix_role_schedules_owner": (["workspace_id", "owner_user_id", "role_key"], True),
        }
        assert "role_schedules" not in sa.inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM agent_queue_items")).scalar() == 0
        with pytest.raises(sa.exc.IntegrityError):
            _queue_item(engine, _SCHEDULED_RUN_KIND)
    finally:
        engine.dispose()
        clear_engine_cache()
