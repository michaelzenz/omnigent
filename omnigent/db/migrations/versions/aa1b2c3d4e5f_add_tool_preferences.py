"""Add deployment-wide tool preferences table.

Revision ID: aa1b2c3d4e5f
Revises: zz2q3r4s5t6u
Create Date: 2026-08-22 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "aa1b2c3d4e5f"
down_revision: str | None = "zz2q3r4s5t6u"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_preferences",
        sa.Column("id", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("disabled_tools", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_tool_preferences_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO tool_preferences (id, disabled_tools, updated_at, updated_by) "
            "VALUES (1, '[]', NULL, NULL)"
        )
    )


def downgrade() -> None:
    op.drop_table("tool_preferences")
