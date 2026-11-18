"""Migration ``8e2b6d4f1a93`` routes GlobalHub events to the sessions that follow their sources.

The upgrade moves each event routed to a manager to the manager's session, renames
the session event source and replaces task subscriptions with session
subscriptions. The downgrade maps the sessions back to managers and deletes the
queue items of the two new kinds.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_PREVIOUS = "5d8e1f3a7c20"
_REVISION = "8e2b6d4f1a93"
_UNROUTED, _ROUTED, _DISMISSED = 4, 6, 8
_EVENTS_KIND, _WORKER_MESSAGE_KIND, _SOURCE_TYPES_KIND = 1, 5, 6
_MANAGER = uuid.uuid4().bytes
_MANAGER_SESSION = uuid.uuid4().bytes
_SESSIONLESS_MANAGER = uuid.uuid4().bytes
_WORKER_SESSION = uuid.uuid4().bytes
_QUEUE_ITEM = uuid.uuid4().bytes
_PR = "https://github.com/example-org/repo/pull/12"


def _migrate(uri: str, engine: sa.Engine, step: str, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        getattr(command, step)(config, revision)


def _execute(engine: sa.Engine, statement: str, **params: Any) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(statement), params)


def _manager(engine: sa.Engine, manager_id: bytes, conversation_id: bytes | None) -> None:
    _execute(
        engine,
        "INSERT INTO managers (id, conversation_id, title, description, created_at, updated_at) "
        "VALUES (:id, :conversation_id, 'Storage', '', 0, 0)",
        id=manager_id,
        conversation_id=conversation_id,
    )


def _event(
    engine: sa.Engine, target_column: str, target: bytes | None, *, state: int, source: str
) -> bytes:
    event_id = uuid.uuid4().bytes
    _execute(
        engine,
        "INSERT INTO task_events (id, event_type, title, source, tags, state, "
        f"{target_column}, queue_item_id, delivery_count, created_at, routed_at) "
        "VALUES (:id, 'session.turn.finished', 'Turn finished', :source, '[]', :state, "
        ":target, :queue_item_id, 2, 0, :routed_at)",
        id=event_id,
        source=source,
        state=state,
        target=target,
        queue_item_id=_QUEUE_ITEM,
        routed_at=None if state == _UNROUTED else 5,
    )
    return event_id


def _events(engine: sa.Engine, target_column: str) -> dict[bytes, tuple[Any, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                f"SELECT id, state, {target_column}, queue_item_id, delivery_count, routed_at, "
                "source FROM task_events"
            )
        ).all()
    return {event_id: tuple(values) for event_id, *values in rows}


def _queue_item(engine: sa.Engine, kind: int) -> None:
    _execute(
        engine,
        "INSERT INTO agent_queue_items (id, target_session_id, kind, seq, created_at) "
        "VALUES (:id, :target, :kind, 1, 0)",
        id=uuid.uuid4().bytes,
        target=_MANAGER_SESSION,
        kind=kind,
    )


def _subscribe(engine: sa.Engine, session_id: bytes, source_key: str) -> None:
    _execute(
        engine,
        "INSERT INTO subscriptions (id, session_id, source_type, source_key, created_at) "
        "VALUES (:id, :session_id, 'github.pull_request', :source_key, 0)",
        id=uuid.uuid4().bytes,
        session_id=session_id,
        source_key=source_key,
    )


def _index_columns(engine: sa.Engine, table: str) -> dict[str, list[str]]:
    return {
        index["name"]: index["column_names"] for index in sa.inspect(engine).get_indexes(table)
    }


def _column_names(engine: sa.Engine, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns(table)}


@pytest.fixture
def database(tmp_path: Path) -> Iterator[tuple[str, sa.Engine]]:
    uri = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    engine = sa.create_engine(uri)
    try:
        yield uri, engine
    finally:
        engine.dispose()
        clear_engine_cache()


def _task(engine: sa.Engine, manager_id: bytes | None, *, state: int) -> bytes:
    task_id = uuid.uuid4().bytes
    _execute(
        engine,
        "INSERT INTO tasks (id, manager_id, owner_user_id, title, state, created_at) "
        "VALUES (:id, :manager_id, 'alice@example.com', 'Fix S3', :state, 0)",
        id=task_id,
        manager_id=manager_id,
        state=state,
    )
    return task_id


def _worker(engine: sa.Engine, task_id: bytes, session_id: bytes) -> None:
    _execute(
        engine,
        "INSERT INTO workers (id, task_id, owner_user_id, session_id, created_at) "
        "VALUES (:id, :task_id, 'alice@example.com', :session_id, 0)",
        id=uuid.uuid4().bytes,
        task_id=task_id,
        session_id=session_id,
    )


def _watch_item(engine: sa.Engine, source: str, source_key: str) -> None:
    _execute(
        engine,
        "INSERT INTO watch_items (id, watcher_id, source, source_key, title, offsets, "
        "created_at, checked_at) VALUES (:id, :watcher_id, :source, :source_key, '', '{}', 0, 0)",
        id=uuid.uuid4().bytes,
        watcher_id=_MANAGER,
        source=source,
        source_key=source_key,
    )


def test_upgrade_subscribes_managers_to_their_live_workers_and_drops_items_without_a_type(
    database: tuple[str, sa.Engine],
) -> None:
    uri, engine = database
    _migrate(uri, engine, "upgrade", _PREVIOUS)
    _manager(engine, _MANAGER, _MANAGER_SESSION)
    _manager(engine, _SESSIONLESS_MANAGER, None)
    active, resolved = _task(engine, _MANAGER, state=1), _task(engine, _MANAGER, state=5)
    archived = _task(engine, _MANAGER, state=4)
    unmanaged = _task(engine, None, state=1)
    sessionless = _task(engine, _SESSIONLESS_MANAGER, state=1)
    second_worker, archived_worker, other_worker = (uuid.uuid4().bytes for _ in range(3))
    for task_id, session_id in (
        (active, _WORKER_SESSION),
        (resolved, _WORKER_SESSION),
        (resolved, second_worker),
        (active, _MANAGER_SESSION),
        (archived, archived_worker),
        (unmanaged, other_worker),
        (sessionless, other_worker),
    ):
        _worker(engine, task_id, session_id)
    _watch_item(engine, "github", _PR)
    _watch_item(engine, "github.pull_request", _PR)

    _migrate(uri, engine, "upgrade", _REVISION)

    with engine.connect() as conn:
        subscriptions = conn.execute(
            sa.text("SELECT session_id, source_type, source_key, owner_user_id FROM subscriptions")
        ).all()
        sources = conn.execute(sa.text("SELECT source FROM watch_items")).scalars().all()
    assert sorted(tuple(row) for row in subscriptions) == sorted(
        [
            (_MANAGER_SESSION, "omnigent.session", _WORKER_SESSION.hex(), "alice@example.com"),
            (_MANAGER_SESSION, "omnigent.session", second_worker.hex(), "alice@example.com"),
        ]
    )
    assert sources == ["github.pull_request"]


def test_upgrade_moves_manager_events_to_their_sessions_and_drops_task_subscriptions(
    database: tuple[str, sa.Engine],
) -> None:
    uri, engine = database
    _migrate(uri, engine, "upgrade", _PREVIOUS)
    _manager(engine, _MANAGER, _MANAGER_SESSION)
    _manager(engine, _SESSIONLESS_MANAGER, None)
    to_manager = _event(engine, "manager_id", _MANAGER, state=_ROUTED, source="session")
    to_sessionless = _event(
        engine, "manager_id", _SESSIONLESS_MANAGER, state=_ROUTED, source="github"
    )
    to_missing = _event(engine, "manager_id", uuid.uuid4().bytes, state=_ROUTED, source="github")
    handled = _event(
        engine, "manager_id", _SESSIONLESS_MANAGER, state=_DISMISSED, source="session"
    )
    waiting = _event(engine, "manager_id", None, state=_UNROUTED, source="slack")
    _execute(
        engine,
        "INSERT INTO task_subscriptions (id, task_id, source_key, created_at) "
        "VALUES (:id, :task_id, :source_key, 0)",
        id=uuid.uuid4().bytes,
        task_id=uuid.uuid4().bytes,
        source_key=_PR,
    )
    _execute(
        engine,
        "INSERT INTO watchers (id, name, description, created_at, updated_at) "
        "VALUES (:id, 'PR watcher', '', 0, 0)",
        id=uuid.uuid4().bytes,
    )
    with pytest.raises(sa.exc.IntegrityError):
        _queue_item(engine, _WORKER_MESSAGE_KIND)

    _migrate(uri, engine, "upgrade", _REVISION)

    assert _events(engine, "target_session_id") == {
        to_manager: (_ROUTED, _MANAGER_SESSION, _QUEUE_ITEM, 2, 5, "omnigent.session"),
        to_sessionless: (_UNROUTED, None, None, 0, None, "github"),
        to_missing: (_UNROUTED, None, None, 0, None, "github"),
        handled: (_DISMISSED, None, _QUEUE_ITEM, 2, 5, "omnigent.session"),
        waiting: (_UNROUTED, None, _QUEUE_ITEM, 2, None, "slack"),
    }
    assert "manager_id" not in _column_names(engine, "task_events")
    event_indexes = _index_columns(engine, "task_events")
    assert "ix_task_events_manager_state" not in event_indexes
    assert event_indexes["ix_task_events_target_state"] == [
        "workspace_id",
        "target_session_id",
        "state",
        "created_at",
        "id",
    ]
    tables = set(sa.inspect(engine).get_table_names())
    assert {"subscriptions", "source_watchers"} <= tables
    assert "task_subscriptions" not in tables
    assert _index_columns(engine, "subscriptions") == {
        "ix_subscriptions_session_key": ["workspace_id", "session_id", "source_key"],
        "ix_subscriptions_key": ["workspace_id", "owner_user_id", "source_key"],
        "ix_subscriptions_type": ["workspace_id", "owner_user_id", "source_type"],
    }
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT builtin FROM watchers")).scalar_one() == 0
    _queue_item(engine, _WORKER_MESSAGE_KIND)
    _queue_item(engine, _SOURCE_TYPES_KIND)
    _subscribe(engine, _MANAGER_SESSION, _PR)
    with pytest.raises(sa.exc.IntegrityError):
        _subscribe(engine, _MANAGER_SESSION, _PR)


def test_downgrade_routes_session_events_back_to_managers_and_drops_the_new_kinds(
    database: tuple[str, sa.Engine],
) -> None:
    uri, engine = database
    _migrate(uri, engine, "upgrade", _REVISION)
    _manager(engine, _MANAGER, _MANAGER_SESSION)
    to_manager = _event(
        engine, "target_session_id", _MANAGER_SESSION, state=_ROUTED, source="omnigent.session"
    )
    to_worker = _event(
        engine, "target_session_id", _WORKER_SESSION, state=_ROUTED, source="github.pull_request"
    )
    handled = _event(
        engine,
        "target_session_id",
        _WORKER_SESSION,
        state=_DISMISSED,
        source="omnigent.session",
    )
    waiting = _event(
        engine, "target_session_id", None, state=_UNROUTED, source="github.pull_request"
    )
    for kind in (_EVENTS_KIND, _WORKER_MESSAGE_KIND, _SOURCE_TYPES_KIND):
        _queue_item(engine, kind)
    _subscribe(engine, _MANAGER_SESSION, _PR)
    _execute(
        engine,
        "INSERT INTO source_watchers (id, source_type, watcher_id, assigned_at) "
        "VALUES (:id, 'github.pull_request', :watcher_id, 0)",
        id=uuid.uuid4().bytes,
        watcher_id=uuid.uuid4().bytes,
    )
    _execute(
        engine,
        "INSERT INTO watchers (id, name, description, builtin, created_at, updated_at) "
        "VALUES (:id, 'Fallback watcher', '', :builtin, 0, 0)",
        id=uuid.uuid4().bytes,
        builtin=True,
    )

    _migrate(uri, engine, "downgrade", _PREVIOUS)

    assert _events(engine, "manager_id") == {
        to_manager: (_ROUTED, _MANAGER, _QUEUE_ITEM, 2, 5, "session"),
        to_worker: (_UNROUTED, None, None, 0, None, "github.pull_request"),
        handled: (_DISMISSED, None, _QUEUE_ITEM, 2, 5, "session"),
        waiting: (_UNROUTED, None, _QUEUE_ITEM, 2, None, "github.pull_request"),
    }
    assert "target_session_id" not in _column_names(engine, "task_events")
    assert _index_columns(engine, "task_events")["ix_task_events_manager_state"] == [
        "workspace_id",
        "manager_id",
        "state",
        "created_at",
        "id",
    ]
    tables = set(sa.inspect(engine).get_table_names())
    assert not {"subscriptions", "source_watchers"} & tables
    assert "builtin" not in _column_names(engine, "watchers")
    with engine.connect() as conn:
        kinds = conn.execute(sa.text("SELECT kind FROM agent_queue_items")).scalars().all()
        task_subscriptions = conn.execute(
            sa.text("SELECT count(*) FROM task_subscriptions")
        ).scalar_one()
    assert (kinds, task_subscriptions) == ([_EVENTS_KIND], 0)
    with pytest.raises(sa.exc.IntegrityError):
        _queue_item(engine, _SOURCE_TYPES_KIND)
