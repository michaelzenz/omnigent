"""Tests for the agent task routing migration (c2d3e4f5a6b7)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from omnigent.db.utils import clear_engine_cache, get_or_create_engine

_PREVIOUS_HEAD = "b1c2d3e4f5a6"


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """Fresh SQLite DB with the full migration chain applied."""
    db_path = tmp_path / "test.db"
    uri = f"sqlite:///{db_path}"
    engine = get_or_create_engine(uri)
    try:
        yield engine
    finally:
        clear_engine_cache()


def test_migration_creates_all_tables(db_engine: Engine) -> None:
    """All task routing tables exist after migrating to head."""
    tables = set(sa.inspect(db_engine).get_table_names())
    assert {
        "tasks",
        "task_tags",
        "task_events",
        "task_event_tags",
        "task_event_routing_attempts",
        "task_event_routing_resolutions",
        "task_event_executions",
    } <= tables


def test_tasks_state_check_enforced(db_engine: Engine) -> None:
    """Invalid task state codes are rejected."""
    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO tasks "
                    "(workspace_id, id, manager_agent_id, title, state, created_at) "
                    "VALUES (0, :id, :manager_id, 't', 99, 1)"
                ),
                {
                    "id": bytes.fromhex("0ecf75a6ff1ff86bcc1902eb0951ef45"),
                    "manager_id": bytes.fromhex("a9930027fd3e2e979e65844f7af7bf88"),
                },
            )


def test_task_events_state_check_enforced(db_engine: Engine) -> None:
    """Invalid task event state codes are rejected."""
    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO task_events "
                    "(workspace_id, id, event_type, title, state, created_at) "
                    "VALUES (0, :id, 'build.finished', 'done', 99, 1)"
                ),
                {"id": bytes.fromhex("11111111111111111111111111111111")},
            )


def test_task_event_routing_attempts_decision_check_enforced(db_engine: Engine) -> None:
    """Invalid routing decision codes are rejected."""
    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO task_event_routing_attempts "
                    "(workspace_id, id, event_id, candidate_task_id, "
                    "candidate_manager_agent_id, rank, decision, proposed_at) "
                    "VALUES (0, :id, :event_id, :task_id, :manager_id, 1, 99, 1)"
                ),
                {
                    "id": bytes.fromhex("22222222222222222222222222222222"),
                    "event_id": bytes.fromhex("11111111111111111111111111111111"),
                    "task_id": bytes.fromhex("33333333333333333333333333333333"),
                    "manager_id": bytes.fromhex("44444444444444444444444444444444"),
                },
            )


def test_task_event_executions_status_check_enforced(db_engine: Engine) -> None:
    """Invalid execution status codes are rejected."""
    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO task_event_executions "
                    "(workspace_id, id, event_id, task_id, manager_agent_id, "
                    "worker_agent_id, status, attempt_no, assigned_at, created_at) "
                    "VALUES (0, :id, :event_id, :task_id, :manager_id, :worker_id, "
                    "99, 1, 1, 1)"
                ),
                {
                    "id": bytes.fromhex("55555555555555555555555555555555"),
                    "event_id": bytes.fromhex("11111111111111111111111111111111"),
                    "task_id": bytes.fromhex("33333333333333333333333333333333"),
                    "manager_id": bytes.fromhex("44444444444444444444444444444444"),
                    "worker_id": bytes.fromhex("66666666666666666666666666666666"),
                },
            )


def test_task_tags_allow_same_tag_on_multiple_tasks(db_engine: Engine) -> None:
    """Multiple tasks may share the same typed tag."""
    task_a = bytes.fromhex("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    task_b = bytes.fromhex("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    with db_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO task_tags "
                "(workspace_id, task_id, tag_type, tag) VALUES "
                "(0, :task_a, 'domain', 's3'), "
                "(0, :task_b, 'domain', 's3')"
            ),
            {"task_a": task_a, "task_b": task_b},
        )
        rows = conn.execute(
            sa.text(
                "SELECT task_id FROM task_tags "
                "WHERE workspace_id = 0 AND tag_type = 'domain' AND tag = 's3'"
            )
        ).fetchall()
    assert len(rows) == 2
