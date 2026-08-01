"""add agent queue tables and dispatch_failed task item state

Revision ID: y4z5a6b7c8d9
Revises: x3y4z5a6b7c8
Create Date: 2026-08-01 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "y4z5a6b7c8d9"
down_revision: str | None = "x3y4z5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the per-agent queue tables and widen the task item state check.

    ``agent_queues`` is the control row (pause/halt, lease, in-flight item) and
    ``agent_queue_items`` holds the agent-ready work units. Both key columns
    ``owner_user_id`` and ``scope_id`` are NOT NULL with an empty-string
    sentinel, because a NULL cannot participate in a primary key.

    ``task_items.state`` gains ``dispatch_failed`` (8), set when the dispatcher
    could not hand an item to an agent at all.
    """
    from omnigent.db.db_models import Uuid16

    op.create_table(
        "agent_queues",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("scope_id", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=True),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.Integer(), nullable=True),
        sa.Column("next_due_at", sa.Integer(), nullable=True),
        sa.Column("inflight_item_id", Uuid16(), nullable=True),
        sa.Column("inflight_since", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "role", "owner_user_id", "scope_id"),
        sa.CheckConstraint("state IN (1, 2, 3)", name="ck_agent_queues_state"),
    )
    op.create_index(
        "ix_agent_queues_due",
        "agent_queues",
        ["workspace_id", "state", "next_due_at", "lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "agent_queue_items",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("scope_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("source_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("not_before", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.Column("dispatched_at", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.CheckConstraint("state IN (1, 2, 3, 4, 5)", name="ck_agent_queue_items_state"),
    )
    op.create_index(
        "ix_agent_queue_items_drain",
        "agent_queue_items",
        [
            "workspace_id",
            "role",
            "owner_user_id",
            "scope_id",
            "state",
            "priority",
            "seq",
        ],
        unique=False,
    )

    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_items_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_items_state",
            "state IN (1, 2, 3, 4, 5, 6, 7, 8)",
        )


def downgrade() -> None:
    """Drop the queue tables and narrow the task item state check.

    Items already in ``dispatch_failed`` are moved back to ``queued`` (4), the
    state they would have been in had the dispatch never been attempted.
    """
    op.get_bind().execute(sa.text("UPDATE task_items SET state = 4 WHERE state = 8"))
    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_items_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_items_state",
            "state IN (1, 2, 3, 4, 5, 6, 7)",
        )

    op.drop_index("ix_agent_queue_items_drain", table_name="agent_queue_items")
    op.drop_table("agent_queue_items")
    op.drop_index("ix_agent_queues_due", table_name="agent_queues")
    op.drop_table("agent_queues")
