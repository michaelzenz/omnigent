"""Tests for the zzh2i3j4k5l6 worker-session dedup migration.

Before the fix, the adoption endpoint deduped against only the session's
oldest worker row, so re-adopting a session for a second task created a
fresh duplicate lane on every update. The migration collapses existing
duplicates per (task_id, target_id), keeps the newest live row, and
re-points references to it.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config

_PRE_DEDUP_REVISION = "zzg1h2i3j4k5"


def _alembic_cfg(uri: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "omnigent/db/migrations")
    cfg.set_main_option("sqlalchemy.url", uri)
    return cfg


def _seed(engine: sa.Engine) -> tuple[str, str, str]:
    """Seed one session bound to two tasks with duplicates on the second."""
    ws = 0
    session = "sess-dup-1"
    task_a = "task-aaaa"
    task_b = "task-bbbb"
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO workers (workspace_id, id, task_id, kind, target_id, state, "
                "needs_response, title, created_at) VALUES "
                "(:ws, 'w-a1', :task_a, 'internal', :session, 'idle', 0, 'on A', 100), "
                "(:ws, 'w-b1', :task_b, 'internal', :session, 'idle', 0, 'B round 1', 200), "
                "(:ws, 'w-b2', :task_b, 'internal', :session, 'idle', 0, 'B round 2', 300), "
                "(:ws, 'w-b3', :task_b, 'internal', :session, 'idle', 0, 'B round 3', 400)"
            ),
            {"ws": ws, "task_a": task_a, "task_b": task_b, "session": session},
        )
        conn.execute(
            sa.text(
                "INSERT INTO task_items (workspace_id, id, task_id, title, state, "
                "worker_id, created_by, kind, created_at) VALUES "
                "(:ws, 'item-1', :task_b, 'Investigate', 1, 'w-b1', 'manager', 'work', 1), "
                "(:ws, 'item-2', :task_b, 'Mitigate', 1, 'w-b3', 'manager', 'work', 2)"
            ),
            {"ws": ws, "task_b": task_b},
        )
        conn.execute(
            sa.text(
                "INSERT INTO agent_queue_items (workspace_id, id, role, owner_user_id, "
                "scope_id, kind, source_ids, state, seq, created_at) VALUES "
                "(:ws, 'q-1', 'worker', 'local', 'w-b1', 'item.dispatch', '[]', 1, 1, 1)"
            ),
            {"ws": ws},
        )
        conn.execute(
            sa.text(
                "INSERT INTO task_assets (workspace_id, id, task_id, kind, category, "
                "title, url, source_worker_id, created_at) VALUES "
                "(:ws, 'asset-1', :task_b, 'workspace', 'workspace', 'repo', '/tmp/repo', "
                "'w-b1', 1)"
            ),
            {"ws": ws, "task_b": task_b},
        )
    return task_a, task_b, session


def test_migration_collapses_duplicate_workers(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    uri = f"sqlite:///{db_path}"
    alembic_command.upgrade(_alembic_cfg(uri), _PRE_DEDUP_REVISION)
    # A plain engine — get_or_create_engine auto-runs migrations to head,
    # which would pre-empt the collapse under test.
    engine = sa.create_engine(uri)
    task_a, task_b, session = _seed(engine)
    alembic_command.upgrade(_alembic_cfg(uri), "head")

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT id, state FROM workers "
                "WHERE workspace_id = 0 AND task_id = :task AND target_id = :session "
                "ORDER BY created_at"
            ),
            {"task": task_b, "session": session},
        ).all()
        kept = [r for r in rows if r[1] != "deleted"]
        dropped = [r for r in rows if r[1] == "deleted"]

        assert kept == [("w-b3", "idle")], kept
        assert dropped == [("w-b1", "deleted"), ("w-b2", "deleted")], dropped

        # The task-A lane is untouched.
        a_rows = conn.execute(
            sa.text(
                "SELECT id, state FROM workers WHERE task_id = :task AND target_id = :session"
            ),
            {"task": task_a, "session": session},
        ).all()
        assert a_rows == [("w-a1", "idle")]

        # References re-pointed to the kept row.
        item_workers = dict(
            conn.execute(
                sa.text("SELECT id, worker_id FROM task_items WHERE task_id = :task"),
                {"task": task_b},
            ).all()
        )
        assert item_workers == {"item-1": "w-b3", "item-2": "w-b3"}

        queue_scopes = (
            conn.execute(sa.text("SELECT scope_id FROM agent_queue_items WHERE id = 'q-1'"))
            .scalars()
            .all()
        )
        assert queue_scopes == ["w-b3"]

        asset_sources = (
            conn.execute(sa.text("SELECT source_worker_id FROM task_assets WHERE id = 'asset-1'"))
            .scalars()
            .all()
        )
        assert asset_sources == ["w-b3"]


def test_migration_leaves_singletons_untouched(tmp_path: Path) -> None:
    """A (task, session) pair with one worker row is not modified."""
    db_path = tmp_path / "test.db"
    uri = f"sqlite:///{db_path}"
    alembic_command.upgrade(_alembic_cfg(uri), _PRE_DEDUP_REVISION)
    engine = sa.create_engine(uri)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO workers (workspace_id, id, task_id, kind, target_id, state, "
                "needs_response, created_at) VALUES "
                "(0, 'w-solo', 'task-solo', 'internal', 'sess-solo', 'busy', 0, 100)"
            )
        )
    alembic_command.upgrade(_alembic_cfg(uri), "head")

    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT id, state FROM workers WHERE id = 'w-solo'")).all()
        assert rows == [("w-solo", "busy")]
