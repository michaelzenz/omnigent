"""add the GlobalHub agent queue

Revision ID: cb3d85b64919
Revises: 8464eb77b8e3
Create Date: 2026-11-13 00:00:00.000000

Adds ``agent_queue_items``, messages waiting for or sent to GlobalHub role
sessions, and ``task_events.queue_item_id``, the item that carries an event.
Ownership is stamped on the row and every read is owner-scoped. Additive;
there are no foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "cb3d85b64919"
down_revision: str | None = "8464eb77b8e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``agent_queue_items`` and add ``task_events.queue_item_id``."""
    op.create_table(
        "agent_queue_items",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("target_session_id", Uuid16(), nullable=False),
        sa.Column("kind", sa.SmallInteger(), nullable=False),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        # CompressedText in the model, which stores zstd frames as binary.
        sa.Column("payload", sa.LargeBinary(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_before", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.Integer(), nullable=True),
        sa.Column("done_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.CheckConstraint("kind IN (1, 2, 3)", name="ck_agent_queue_items_kind"),
        sa.CheckConstraint("state IN (1, 2, 3, 4)", name="ck_agent_queue_items_state"),
    )
    op.create_index(
        "ix_agent_queue_items_state_seq",
        "agent_queue_items",
        ["workspace_id", "state", "seq"],
        unique=False,
    )
    op.create_index(
        "ix_agent_queue_items_target_state",
        "agent_queue_items",
        ["workspace_id", "target_session_id", "state", "seq"],
        unique=False,
    )
    op.add_column("task_events", sa.Column("queue_item_id", Uuid16(), nullable=True))
    op.create_index(
        "ix_task_events_queue_item",
        "task_events",
        ["workspace_id", "queue_item_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop ``task_events.queue_item_id`` and ``agent_queue_items``."""
    op.drop_index("ix_task_events_queue_item", table_name="task_events")
    # DROP COLUMN needs batch_alter_table on older SQLite.
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_column("queue_item_id")
    op.drop_index("ix_agent_queue_items_target_state", table_name="agent_queue_items")
    op.drop_index("ix_agent_queue_items_state_seq", table_name="agent_queue_items")
    op.drop_table("agent_queue_items")
