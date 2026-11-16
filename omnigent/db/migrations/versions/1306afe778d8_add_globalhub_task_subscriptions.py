"""add the GlobalHub task subscriptions table

Revision ID: 1306afe778d8
Revises: 70752d748475
Create Date: 2026-11-15 00:00:00.000000

Adds ``task_subscriptions``: the keys that tasks follow, e.g. a pull request
URL. A task's manager subscribes the task, and an event whose source key a live
task follows goes to that task's manager. ``ix_task_subscriptions_task_key`` is
unique, so a task follows a key once; ``ix_task_subscriptions_key`` finds the
owner's tasks that follow a key. Ownership is stamped on the row; there are no
foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "1306afe778d8"
down_revision: str | None = "70752d748475"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``task_subscriptions`` and its indexes."""
    op.create_table(
        "task_subscriptions",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
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
        unique=False,
    )


def downgrade() -> None:
    """Drop ``task_subscriptions`` and its indexes."""
    op.drop_index("ix_task_subscriptions_key", table_name="task_subscriptions")
    op.drop_index("ix_task_subscriptions_task_key", table_name="task_subscriptions")
    op.drop_table("task_subscriptions")
