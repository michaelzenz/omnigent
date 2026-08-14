"""add task_assets table

Revision ID: i8j9k0l1m2n3
Revises: h7i8j9k0l1m2
Create Date: 2026-07-30 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "i8j9k0l1m2n3"
down_revision: str | None = "h7i8j9k0l1m2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create task_assets for task-card sidebar links and files."""
    op.create_table(
        "task_assets",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.CheckConstraint("kind IN ('url')", name="ck_task_assets_kind"),
    )
    op.create_index(
        "ix_task_assets_task_sort",
        "task_assets",
        ["workspace_id", "task_id", "sort_order", "id"],
    )


def downgrade() -> None:
    """Drop task_assets."""
    op.drop_index("ix_task_assets_task_sort", table_name="task_assets")
    op.drop_table("task_assets")
