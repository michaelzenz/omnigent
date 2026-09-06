"""Repair task event source offsets left as integers.

Revision ID: zz9f0a1b2c3d4
Revises: zz8e9f0a1b2c3
Create Date: 2026-09-06 12:10:00.000000

The source offset type was changed by editing the migration that originally
added the column. Databases that had already applied that migration therefore
kept the old BIGINT type while reporting the current Alembic revision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zz9f0a1b2c3d4"
down_revision: str | None = "zz8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Convert legacy numeric offsets to the canonical string type."""
    columns = {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns("task_events")
    }
    source_offset = columns.get("source_offset")
    if source_offset is None:
        with op.batch_alter_table("task_events") as batch_op:
            batch_op.add_column(sa.Column("source_offset", sa.String(512), nullable=True))
        return

    current_type = source_offset["type"]
    if not isinstance(current_type, sa.String) or current_type.length != 512:
        with op.batch_alter_table("task_events") as batch_op:
            batch_op.alter_column(
                "source_offset",
                existing_type=current_type,
                type_=sa.String(512),
                existing_nullable=True,
                postgresql_using="source_offset::varchar(512)",
            )


def downgrade() -> None:
    """Keep the canonical type owned by the original migration."""
