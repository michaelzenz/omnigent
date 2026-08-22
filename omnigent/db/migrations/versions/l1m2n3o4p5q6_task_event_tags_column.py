"""store task event tags inline; drop search_text

Revision ID: l1m2n3o4p5q6
Revises: k0l1m2n3o4p5
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l1m2n3o4p5q6"
down_revision: str | None = "k0l1m2n3o4p5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_events",
        sa.Column("tags", sa.Text(), nullable=False, server_default="[]"),
    )
    op.drop_index("ix_task_event_tags_reverse", table_name="task_event_tags")
    op.drop_table("task_event_tags")
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_column("search_text")


def downgrade() -> None:
    from omnigent.db.db_models import Uuid16

    op.create_table(
        "task_event_tags",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("event_id", Uuid16(), nullable=False),
        sa.Column("tag_type", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "event_id", "tag_type", "tag"),
    )
    op.create_index(
        "ix_task_event_tags_reverse",
        "task_event_tags",
        ["workspace_id", "tag_type", "tag", "event_id"],
        unique=False,
    )
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        )
        batch_op.drop_column("tags")
