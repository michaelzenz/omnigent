"""route GlobalHub events to the sessions that subscribe to their sources

Revision ID: 8e2b6d4f1a93
Revises: 5d8e1f3a7c20
Create Date: 2026-11-17 23:00:00.000000

Adds ``subscriptions``: the sources that sessions follow, one row per session
and source key. Each event about a key goes to every session that follows it.
Adds ``source_watchers``: the watcher that the custodian assigned to each
source type. ``watchers`` gets a ``builtin`` flag for the fallback watcher.

``task_events`` routes to a session: ``target_session_id`` replaces
``manager_id``, with an event routed to a manager moved to the manager's
session. A routed event with no session to go to waits for the broker again.
Session events take the source type ``"omnigent.session"``.

``task_subscriptions`` goes away without a copy: a task no longer follows a
key, and a session that needs a source's events subscribes to it again.
The manager of each live task gets a subscription to each of the task's
worker sessions, so finished turns keep reaching it. Watch items with a source
that is not a source type (no dot, e.g. ``"github"``) go away; their watchers
see them again as new items. ``agent_queue_items`` accepts two more kinds: a
manager's message for a worker (5) and a request that the custodian assign
source types (6). Ownership is stamped on each row; there are no foreign-key
constraints (Rule R032). Batch mode keeps the column changes working on SQLite.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "8e2b6d4f1a93"
down_revision: str | None = "5d8e1f3a7c20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_COLUMNS = ["state", "created_at", "id"]
_UNROUTED = 4
_ROUTED = 6
_LIVE_TASK_STATES = (1, 3, 5)
_SESSION_SOURCE_TYPE = "omnigent.session"
_NOTE_MAX_CHARS = 3000


def _backfill_worker_subscriptions() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT tasks.workspace_id, tasks.owner_user_id, tasks.title, "
            "managers.conversation_id, workers.session_id FROM tasks "
            "JOIN managers ON managers.workspace_id = tasks.workspace_id "
            "AND managers.id = tasks.manager_id "
            "JOIN workers ON workers.workspace_id = tasks.workspace_id "
            "AND workers.task_id = tasks.id "
            "WHERE tasks.state IN :live AND managers.conversation_id IS NOT NULL"
        ).bindparams(sa.bindparam("live", expanding=True)),
        {"live": list(_LIVE_TASK_STATES)},
    ).fetchall()
    insert = sa.text(
        "INSERT INTO subscriptions (workspace_id, id, owner_user_id, session_id, "
        "source_type, source_key, note, created_at) VALUES (:workspace_id, :id, "
        ":owner_user_id, :session_id, :source_type, :source_key, :note, :created_at)"
    )
    now = int(time.time())
    seen: set[tuple[int, bytes, str]] = set()
    for workspace_id, owner_user_id, title, manager_session, worker_session in rows:
        manager_bytes, worker_bytes = bytes(manager_session), bytes(worker_session)
        identity = (workspace_id, manager_bytes, worker_bytes.hex())
        if manager_bytes == worker_bytes or identity in seen:
            continue
        seen.add(identity)
        bind.execute(
            insert,
            {
                "workspace_id": workspace_id,
                "id": uuid.uuid4().bytes,
                "owner_user_id": owner_user_id,
                "session_id": manager_bytes,
                "source_type": _SESSION_SOURCE_TYPE,
                "source_key": worker_bytes.hex(),
                "note": f"Worker on the task {title}"[:_NOTE_MAX_CHARS],
                "created_at": now,
            },
        )


def _reset_stranded_events(target_column: str) -> None:
    op.execute(
        sa.text(
            "UPDATE task_events SET state = :unrouted, queue_item_id = NULL, "
            "delivery_count = 0, routed_at = NULL "
            f"WHERE state = :routed AND {target_column} IS NULL"
        ).bindparams(unrouted=_UNROUTED, routed=_ROUTED)
    )


def upgrade() -> None:
    """Create the session subscriptions, route events to sessions, and drop task subscriptions."""
    op.create_table(
        "subscriptions",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("session_id", Uuid16(), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(512), nullable=False),
        sa.Column("note", sa.String(3000), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_subscriptions_session_key",
        "subscriptions",
        ["workspace_id", "session_id", "source_key"],
        unique=True,
    )
    op.create_index(
        "ix_subscriptions_key", "subscriptions", ["workspace_id", "owner_user_id", "source_key"]
    )
    op.create_index(
        "ix_subscriptions_type", "subscriptions", ["workspace_id", "owner_user_id", "source_type"]
    )
    op.create_table(
        "source_watchers",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("watcher_id", Uuid16(), nullable=False),
        sa.Column("assigned_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_source_watchers_type",
        "source_watchers",
        ["workspace_id", "owner_user_id", "source_type"],
    )
    op.create_index(
        "ix_source_watchers_watcher", "source_watchers", ["workspace_id", "watcher_id"]
    )
    with op.batch_alter_table("watchers") as batch_op:
        batch_op.add_column(
            sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.drop_index("ix_task_events_manager_state", table_name="task_events")
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.add_column(sa.Column("target_session_id", Uuid16(), nullable=True))
    op.execute(
        "UPDATE task_events SET target_session_id = ("
        "SELECT managers.conversation_id FROM managers "
        "WHERE managers.workspace_id = task_events.workspace_id "
        "AND managers.id = task_events.manager_id) "
        "WHERE manager_id IS NOT NULL"
    )
    _reset_stranded_events("target_session_id")
    op.execute("UPDATE task_events SET source = 'omnigent.session' WHERE source = 'session'")
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_column("manager_id")
    op.create_index(
        "ix_task_events_target_state",
        "task_events",
        ["workspace_id", "target_session_id", *_EVENT_COLUMNS],
    )

    op.drop_index("ix_task_subscriptions_key", table_name="task_subscriptions")
    op.drop_index("ix_task_subscriptions_task_key", table_name="task_subscriptions")
    op.drop_table("task_subscriptions")
    _backfill_worker_subscriptions()
    op.execute("DELETE FROM watch_items WHERE source NOT LIKE '%.%'")

    with op.batch_alter_table("agent_queue_items") as batch_op:
        batch_op.drop_constraint("ck_agent_queue_items_kind", type_="check")
        batch_op.create_check_constraint("ck_agent_queue_items_kind", "kind IN (1, 2, 3, 4, 5, 6)")


def downgrade() -> None:
    """Bring back task subscriptions and manager routing, empty; drop the session subscriptions."""
    op.execute("DELETE FROM agent_queue_items WHERE kind IN (5, 6)")
    with op.batch_alter_table("agent_queue_items") as batch_op:
        batch_op.drop_constraint("ck_agent_queue_items_kind", type_="check")
        batch_op.create_check_constraint("ck_agent_queue_items_kind", "kind IN (1, 2, 3, 4)")

    op.create_table(
        "task_subscriptions",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("source_key", sa.String(512), nullable=False),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_task_subscriptions_task_key",
        "task_subscriptions",
        ["workspace_id", "task_id", "source_key"],
        unique=True,
    )
    op.create_index(
        "ix_task_subscriptions_key",
        "task_subscriptions",
        ["workspace_id", "owner_user_id", "source_key"],
    )

    op.drop_index("ix_task_events_target_state", table_name="task_events")
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.add_column(sa.Column("manager_id", Uuid16(), nullable=True))
    op.execute(
        "UPDATE task_events SET manager_id = ("
        "SELECT managers.id FROM managers "
        "WHERE managers.workspace_id = task_events.workspace_id "
        "AND managers.conversation_id = task_events.target_session_id) "
        "WHERE target_session_id IS NOT NULL"
    )
    _reset_stranded_events("manager_id")
    op.execute("UPDATE task_events SET source = 'session' WHERE source = 'omnigent.session'")
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_column("target_session_id")
    op.create_index(
        "ix_task_events_manager_state",
        "task_events",
        ["workspace_id", "manager_id", *_EVENT_COLUMNS],
    )

    with op.batch_alter_table("watchers") as batch_op:
        batch_op.drop_column("builtin")
    op.drop_index("ix_source_watchers_watcher", table_name="source_watchers")
    op.drop_index("ix_source_watchers_type", table_name="source_watchers")
    op.drop_table("source_watchers")
    op.drop_index("ix_subscriptions_type", table_name="subscriptions")
    op.drop_index("ix_subscriptions_key", table_name="subscriptions")
    op.drop_index("ix_subscriptions_session_key", table_name="subscriptions")
    op.drop_table("subscriptions")
