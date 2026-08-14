"""task_assets: integer id, drop sort_order

Revision ID: s8t9u0v1w2x3
Revises: r7s8t9u0v1w2
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "s8t9u0v1w2x3"
down_revision: str | None = "r7s8t9u0v1w2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace UUID asset ids and sort_order with monotonic integer ids."""
    op.drop_index("ix_task_assets_task_sort", table_name="task_assets")
    op.drop_table("task_assets")
    op.create_table(
        "task_assets",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.CheckConstraint("kind IN ('url')", name="ck_task_assets_kind"),
    )
    op.create_index(
        "ix_task_assets_task",
        "task_assets",
        ["workspace_id", "task_id", "id"],
    )


def downgrade() -> None:
    """Restore UUID asset ids and sort_order."""
    op.drop_index("ix_task_assets_task", table_name="task_assets")
    op.drop_table("task_assets")
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
