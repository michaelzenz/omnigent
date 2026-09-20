"""Add title to workers for the manager-maintained work label.

Revision ID: zzb1c2d3e4f5
Revises: zza0b1c2d3e4
Create Date: 2026-09-07 00:00:00.000000

Adds a nullable ``title`` column to the ``workers`` table. Managers keep it
updated with what the worker is currently working on so the task card shows
recent context instead of the static provider name.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "zzb1c2d3e4f5"
down_revision: str | None = "zza0b1c2d3e4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("workers", sa.Column("title", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workers") as batch_op:
        batch_op.drop_column("title")
