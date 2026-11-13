"""add the GlobalHub task events table

Revision ID: 777a6c68d35a
Revises: 4bcc2b50a77d
Create Date: 2026-11-13 00:00:00.000000

Adds ``task_events``: signals on a GlobalHub board that the broker routes to a
manager. Ownership is stamped on the row and every read is owner-scoped.
Additive; there are no foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "777a6c68d35a"
down_revision: str | None = "4bcc2b50a77d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``task_events`` table."""
    op.create_table(
        "task_events",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        # CompressedText in the model, which stores zstd frames as binary.
        sa.Column("payload", sa.LargeBinary(), nullable=True),
        sa.Column("source", sa.String(256), nullable=True),
        sa.Column("source_key", sa.String(512), nullable=True),
        sa.Column("source_offset", sa.String(512), nullable=True),
        sa.Column("source_session_id", Uuid16(), nullable=True),
        # No server default: MySQL rejects one on TEXT.
        sa.Column("tags", sa.Text(), nullable=False),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="4"),
        sa.Column("task_id", Uuid16(), nullable=True),
        sa.Column("manager_id", Uuid16(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.Column("routed_at", sa.Integer(), nullable=True),
        sa.Column("processed_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.CheckConstraint("state IN (4, 6, 8, 12)", name="ck_task_events_state"),
    )
    op.create_index(
        "ix_task_events_owner_state",
        "task_events",
        ["workspace_id", "owner_user_id", "state", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_events_manager_state",
        "task_events",
        ["workspace_id", "manager_id", "state", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_events_source_key",
        "task_events",
        ["workspace_id", "owner_user_id", "source_key"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the ``task_events`` table."""
    op.drop_index("ix_task_events_source_key", table_name="task_events")
    op.drop_index("ix_task_events_manager_state", table_name="task_events")
    op.drop_index("ix_task_events_owner_state", table_name="task_events")
    op.drop_table("task_events")
