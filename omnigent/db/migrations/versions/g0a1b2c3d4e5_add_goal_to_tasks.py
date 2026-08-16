"""add non-nullable goal column to tasks

Revision ID: g0a1b2c3d4e5
Revises: s0a1b2c3d4e5
Create Date: 2026-08-16 00:00:00.000000

Adds ``tasks.goal`` — the endstate the task should land on, distinct from
``description`` (current status). Required for every row; existing tasks are
wiped since they predate the field.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g0a1b2c3d4e5"
down_revision: str | None = "s0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM tasks")
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("goal", sa.Text(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("goal")
