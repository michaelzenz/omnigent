"""Tests for the shape of the agent task tables at migration head."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from omnigent.db.utils import _build_alembic_config, clear_engine_cache, get_or_create_engine

_PREVIOUS_HEAD = "b1c2d3e4f5a6"
_MANAGERS_PREVIOUS_REVISION = "b6d7e8f9a0c1"
_MANAGERS_REVISION = "c7d8e9f0a1b3"


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
        "managers",
        "tasks",
        "task_tags",
        "task_events",
        "task_event_routing_attempts",
        "task_event_executions",
        "task_items",
        "task_item_events",
        "task_role_profiles",
        "user_role_sessions",
    } <= tables
    columns = {column["name"] for column in sa.inspect(db_engine).get_columns("task_events")}
    assert "tags" in columns
    assert "priority" not in columns
    assert "summary" not in columns
    assert "source_internal_session_id" in columns
    assert "manager_conversation_id" in columns
    assert "source_session_id" not in columns
    assert "selected_routing_attempt_id" not in columns
    assert "search_text" not in columns
    assert "task_event_tags" not in tables
    task_column_defs = {
        column["name"]: column for column in sa.inspect(db_engine).get_columns("tasks")
    }
    task_columns = set(task_column_defs)
    assert "search_text" not in task_columns
    assert "manager_agent_id" not in task_columns
    # A task names the roles that run it; the agent behind each role lives on
    # the role definition.
    assert "agent_profile_id" not in task_columns
    assert {"manager_role_key", "worker_role_key"} <= task_columns
    assert task_column_defs["goal"]["nullable"] is False
    task_indexes = {index["name"] for index in sa.inspect(db_engine).get_indexes("tasks")}
    assert "ix_tasks_manager_role_key" in task_indexes
    assert "ix_tasks_agent_profile_id" not in task_indexes
    event_indexes = {
        index["name"] for index in sa.inspect(db_engine).get_indexes("task_events")
    }
    assert "ix_task_events_manager_state" in event_indexes

    manager_columns = {
        column["name"] for column in sa.inspect(db_engine).get_columns("managers")
    }
    assert {
        "workspace_id",
        "conversation_id",
        "owner_user_id",
        "role_key",
        "description",
        "created_at",
        "updated_at",
    } == manager_columns
    manager_indexes = {
        index["name"] for index in sa.inspect(db_engine).get_indexes("managers")
    }
    assert "ix_managers_owner" in manager_indexes


def test_manager_backfill_detaches_shared_conversation_from_other_owners(
    tmp_path: Path,
) -> None:
    uri = f"sqlite:///{tmp_path / 'manager-backfill.db'}"
    engine = sa.create_engine(uri)
    config = _build_alembic_config(uri)
    manager_id = bytes.fromhex("abababababababababababababababab")
    anonymous_task = bytes.fromhex("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    alice_task = bytes.fromhex("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    alice_event = bytes.fromhex("cccccccccccccccccccccccccccccccc")
    try:
        with engine.begin() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, _MANAGERS_PREVIOUS_REVISION)
            tasks = sa.Table("tasks", sa.MetaData(), autoload_with=conn)
            permissions = sa.Table(
                "session_permissions",
                sa.MetaData(),
                autoload_with=conn,
            )
            events = sa.Table("task_events", sa.MetaData(), autoload_with=conn)
            conn.execute(
                tasks.insert(),
                [
                    {
                        "id": anonymous_task,
                        "manager_conversation_id": manager_id,
                        "owner_user_id": None,
                        "manager_role_key": "manager:default",
                        "title": "Anonymous",
                        "goal": "Anonymous goal",
                        "state": 1,
                        "created_at": 2,
                    },
                    {
                        "id": alice_task,
                        "manager_conversation_id": manager_id,
                        "owner_user_id": "alice",
                        "manager_role_key": "manager:default",
                        "title": "Alice",
                        "goal": "Alice goal",
                        "state": 1,
                        "created_at": 1,
                    },
                ],
            )
            conn.execute(
                permissions.insert(),
                {
                    "user_id": "alice",
                    "conversation_id": manager_id,
                    "level": 4,
                },
            )
            conn.execute(
                events.insert(),
                {
                    "id": alice_event,
                    "task_id": alice_task,
                    "event_type": "build.failed",
                    "title": "Alice event",
                    "state": 5,
                    "created_at": 1,
                },
            )
            command.upgrade(config, _MANAGERS_REVISION)

            manager_owner = conn.execute(
                sa.text("SELECT owner_user_id FROM managers")
            ).scalar_one()
            bindings = dict(
                conn.execute(
                    sa.text(
                        "SELECT id, manager_conversation_id FROM tasks "
                        "WHERE id IN (:anonymous_task, :alice_task)"
                    ),
                    {
                        "anonymous_task": anonymous_task,
                        "alice_task": alice_task,
                    },
                )
            )
            event_manager, event_owner = conn.execute(
                sa.text(
                    "SELECT manager_conversation_id, owner_user_id FROM task_events "
                    "WHERE id = :event_id"
                ),
                {"event_id": alice_event},
            ).one()

        assert manager_owner == "alice"
        assert bindings[anonymous_task] is None
        assert bindings[alice_task] == manager_id
        assert event_manager == manager_id
        assert event_owner == "alice"
    finally:
        engine.dispose()


def test_role_definitions_are_global_and_sessions_per_user(db_engine: Engine) -> None:
    """Role definitions lost their owner; live sessions kept one."""
    tables = set(sa.inspect(db_engine).get_table_names())
    assert "user_task_role_profiles" not in tables
    role_columns = {
        column["name"] for column in sa.inspect(db_engine).get_columns("task_role_profiles")
    }
    assert {"role", "kind", "agent_profile_id", "harness", "model"} <= role_columns
    assert "user_id" not in role_columns
    assert "conversation_id" not in role_columns
    session_columns = {
        column["name"] for column in sa.inspect(db_engine).get_columns("user_role_sessions")
    }
    assert {"user_id", "role", "conversation_id"} <= session_columns


def test_workers_carry_a_role_or_an_agent(db_engine: Engine) -> None:
    """Managed lanes resolve their agent through a role; adopted ones name it."""
    worker_columns = {column["name"] for column in sa.inspect(db_engine).get_columns("workers")}
    assert {"role_key", "agent_profile_id"} <= worker_columns
    assert "profile_id" not in worker_columns

    task_id = bytes.fromhex("cccccccccccccccccccccccccccccccc")
    agent_id = bytes.fromhex("dddddddddddddddddddddddddddddddd")
    with db_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO workers "
                "(workspace_id, id, task_id, role_key, agent_profile_id, kind, created_at) "
                "VALUES (0, :id, :task_id, 'worker:default', NULL, 'managed', 1)"
            ),
            {"id": bytes.fromhex("e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1"), "task_id": task_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO workers "
                "(workspace_id, id, task_id, role_key, agent_profile_id, kind, created_at) "
                "VALUES (0, :id, :task_id, NULL, :agent_id, 'external', 1)"
            ),
            {
                "id": bytes.fromhex("e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2"),
                "task_id": task_id,
                "agent_id": agent_id,
            },
        )

    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO workers "
                    "(workspace_id, id, task_id, role_key, agent_profile_id, kind, created_at) "
                    "VALUES (0, :id, :task_id, 'worker:default', :agent_id, 'managed', 1)"
                ),
                {
                    "id": bytes.fromhex("e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3"),
                    "task_id": task_id,
                    "agent_id": agent_id,
                },
            )

    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO workers "
                    "(workspace_id, id, task_id, role_key, agent_profile_id, kind, created_at) "
                    "VALUES (0, :id, :task_id, 'worker:default', NULL, 'external', 1)"
                ),
                {
                    "id": bytes.fromhex("e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4"),
                    "task_id": task_id,
                },
            )


def test_tasks_state_check_enforced(db_engine: Engine) -> None:
    """Invalid task state codes are rejected."""
    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO tasks "
                    "(workspace_id, id, manager_role_key, worker_role_key, title, goal, "
                    "state, created_at) "
                    "VALUES (0, :id, 'manager:default', 'worker:default', 't', 'g', 99, 1)"
                ),
                {"id": bytes.fromhex("0ecf75a6ff1ff86bcc1902eb0951ef45")},
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


def test_task_events_state_8_allowed(db_engine: Engine) -> None:
    """The dismissed state code is accepted."""
    with db_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO task_events "
                "(workspace_id, id, event_type, title, state, created_at) "
                "VALUES (0, :id, 'build.finished', 'done', 8, 1)"
            ),
            {"id": bytes.fromhex("22222222222222222222222222222222")},
        )


def test_task_events_state_9_allowed(db_engine: Engine) -> None:
    """The failed state code is accepted."""
    with db_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO task_events "
                "(workspace_id, id, event_type, title, state, created_at) "
                "VALUES (0, :id, 'build.finished', 'done', 9, 1)"
            ),
            {"id": bytes.fromhex("33333333333333333333333333333333")},
        )


def test_task_events_state_10_rejected(db_engine: Engine) -> None:
    """The removed pending state code is rejected."""
    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO task_events "
                    "(workspace_id, id, event_type, title, state, created_at) "
                    "VALUES (0, :id, 'session.adoption', 'retry', 10, 1)"
                ),
                {"id": bytes.fromhex("44444444444444444444444444444444")},
            )


def test_task_events_state_2_rejected(db_engine: Engine) -> None:
    """The removed routing state code is rejected."""
    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO task_events "
                    "(workspace_id, id, event_type, title, state, created_at) "
                    "VALUES (0, :id, 'build.finished', 'done', 2, 1)"
                ),
                {"id": bytes.fromhex("45454545454545454545454545454545")},
            )


def test_task_events_state_3_rejected(db_engine: Engine) -> None:
    """The removed awaiting_user_selection state code is rejected."""
    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO task_events "
                    "(workspace_id, id, event_type, title, state, created_at) "
                    "VALUES (0, :id, 'build.finished', 'done', 3, 1)"
                ),
                {"id": bytes.fromhex("46464646464646464646464646464646")},
            )


def test_task_event_executions_status_check_enforced(db_engine: Engine) -> None:
    """Invalid execution status codes are rejected."""
    with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO task_event_executions "
                    "(workspace_id, id, task_item_id, task_id, status, attempt_no, "
                    "assigned_at, created_at) "
                    "VALUES (0, :id, :task_item_id, :task_id, 99, 1, 1, 1)"
                ),
                {
                    "id": bytes.fromhex("55555555555555555555555555555555"),
                    "task_item_id": bytes.fromhex("77777777777777777777777777777777"),
                    "task_id": bytes.fromhex("33333333333333333333333333333333"),
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
