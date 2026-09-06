"""Drop legacy role and archived state from agents.

Revision ID: zz7d8e9f0a1b2
Revises: zz6c7d8e9f0a1
Create Date: 2026-09-06 11:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zz7d8e9f0a1b2"
down_revision: str | None = "zz6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM agents WHERE is_role = true OR archived = true")
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("is_role")
        batch_op.drop_column("archived")


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(
            sa.Column("is_role", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false())
        )
