"""drop timer_items table

Revision ID: d9a3b02f8e51
Revises: c8f2a91e7d40
Create Date: 2026-08-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d9a3b02f8e51"
down_revision: str | None = "c8f2a91e7d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop timer_items — replaced by host-side script timer plugins."""
    op.drop_index("ix_timer_items_owner_created", table_name="timer_items")
    op.drop_index("ix_timer_items_host_due", table_name="timer_items")
    op.drop_table("timer_items")


def downgrade() -> None:
    """Recreate timer_items (best-effort; not used after deprecation)."""
    import sqlalchemy as sa

    from omnigent.db.db_models import Uuid16

    op.create_table(
        "timer_items",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("fire_at", sa.Integer(), nullable=False),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("owner_user_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("fired_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("state IN (1, 2, 3, 4)", name="ck_timer_items_state"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_timer_items_host_due",
        "timer_items",
        ["workspace_id", "host_id", "state", "fire_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_timer_items_owner_created",
        "timer_items",
        ["workspace_id", "owner_user_id", "created_at", "id"],
        unique=False,
    )
