"""drop task_events.summary and rename source_session_id

Revision ID: p5q6r7s8t9u0
Revises: o4p5q6r7s8t9
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p5q6r7s8t9u0"
down_revision: str | None = "o4p5q6r7s8t9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_column("summary")
        batch_op.alter_column(
            "source_session_id",
            new_column_name="source_internal_session_id",
        )


def downgrade() -> None:
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.alter_column(
            "source_internal_session_id",
            new_column_name="source_session_id",
        )
        batch_op.add_column(sa.Column("summary", sa.Text(), nullable=True))
