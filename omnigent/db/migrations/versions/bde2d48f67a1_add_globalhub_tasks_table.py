"""add the GlobalHub tasks table

Revision ID: bde2d48f67a1
Revises: a7d4c2e9f610
Create Date: 2026-11-12 00:00:00.000000

Adds ``tasks``: the cards on each user's GlobalHub board. Ownership is stamped
on the row and every read is owner-scoped. Additive; there are no foreign-key
constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "bde2d48f67a1"
down_revision: str | None = "a7d4c2e9f610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``tasks`` table."""
    op.create_table(
        "tasks",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("manager_id", Uuid16(), nullable=True),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        # CompressedText in the model, which stores zstd frames as binary.
        sa.Column("description", sa.LargeBinary(), nullable=True),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("queue_rank", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.CheckConstraint("state IN (1, 3, 4, 5)", name="ck_tasks_state"),
        sa.CheckConstraint("priority BETWEEN 0 AND 3", name="ck_tasks_priority"),
    )
    op.create_index(
        "ix_tasks_owner_board",
        "tasks",
        ["workspace_id", "owner_user_id", "state", "queue_rank", "id"],
        unique=False,
    )
    op.create_index("ix_tasks_manager", "tasks", ["workspace_id", "manager_id"], unique=False)


def downgrade() -> None:
    """Drop the ``tasks`` table."""
    op.drop_index("ix_tasks_manager", table_name="tasks")
    op.drop_index("ix_tasks_owner_board", table_name="tasks")
    op.drop_table("tasks")
