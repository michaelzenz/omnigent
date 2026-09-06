"""Repair hosts missing the pending sandbox termination column.

Revision ID: zz8e9f0a1b2c3
Revises: zz7d8e9f0a1b2
Create Date: 2026-09-06 12:05:00.000000

Some databases reached the manager-identity migration before its ancestry
was updated to include gb1b2c3d4e5f. Alembic therefore considers those
databases current even though that migration never added the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zz8e9f0a1b2c3"
down_revision: str | None = "zz7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the column only to databases affected by the ancestry rewrite."""
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("hosts")}
    if "terminating_sandbox_id" not in columns:
        with op.batch_alter_table("hosts") as batch_op:
            batch_op.add_column(
                sa.Column("terminating_sandbox_id", sa.String(256), nullable=True)
            )


def downgrade() -> None:
    """Keep the column owned by the earlier canonical migration."""
