"""add persistent memory categories

Revision ID: m1b2c3d4e5f6
Revises: k0a1b2c3d4e5
Create Date: 2026-08-18 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.compression import CompressedText
from omnigent.db.db_models import Uuid16

revision: str = "m1b2c3d4e5f6"
down_revision: str | None = "k0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_categories",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("content", CompressedText(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_memory_categories_user_order",
        "memory_categories",
        ["workspace_id", "user_id", "display_order", "id"],
        unique=False,
    )
    op.create_table(
        "memory_settings",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("memory_settings")
    op.drop_index("ix_memory_categories_user_order", table_name="memory_categories")
    op.drop_table("memory_categories")
