"""task items kind (work vs human_action)

Revision ID: k1l2m3n4o5p6
Revises: f0e1d2c3b4a5
Create Date: 2026-08-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k1l2m3n4o5p6"
down_revision: str | None = "f0e1d2c3b4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="work")
        )


def downgrade() -> None:
    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.drop_column("kind")
