"""drop unused task_events denormalized columns

Revision ID: o4p5q6r7s8t9
Revises: n3o4p5q6r7s8
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "o4p5q6r7s8t9"
down_revision: str | None = "n3o4p5q6r7s8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_task_events_manager_agent_state", table_name="task_events")
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_column("manager_conversation_id")
        batch_op.drop_column("manager_agent_id")
        batch_op.drop_column("priority")


def downgrade() -> None:
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("manager_agent_id", Uuid16(), nullable=True))
        batch_op.add_column(sa.Column("manager_conversation_id", Uuid16(), nullable=True))
    op.create_index(
        "ix_task_events_manager_agent_state",
        "task_events",
        ["workspace_id", "manager_agent_id", "state", "id"],
        unique=False,
    )
