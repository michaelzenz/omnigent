"""add the GlobalHub task assets table

Revision ID: a39c579e21ca
Revises: db829cae8d09
Create Date: 2026-11-13 00:00:00.000000

Adds ``task_assets``: links a GlobalHub task carries, one per URL on a task.
Ownership is stamped on the row and every read is owner-scoped. Additive;
there are no foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "a39c579e21ca"
down_revision: str | None = "db829cae8d09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``task_assets`` table."""
    op.create_table(
        "task_assets",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        # No server default: MySQL rejects one on TEXT.
        sa.Column("related_session_ids", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_task_assets_task",
        "task_assets",
        ["workspace_id", "task_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the ``task_assets`` table."""
    op.drop_index("ix_task_assets_task", table_name="task_assets")
    op.drop_table("task_assets")
