"""link worker executions to agent queue items

Revision ID: ab3c4d5e6f7g
Revises: zz2q3r4s5t6u
Create Date: 2026-08-22 12:36:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "ab3c4d5e6f7g"
down_revision: str | None = "zz2q3r4s5t6u"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_event_executions",
        sa.Column("agent_queue_item_id", Uuid16(), nullable=True),
    )
    op.create_index(
        "uq_task_event_executions_agent_queue_item",
        "task_event_executions",
        ["workspace_id", "agent_queue_item_id"],
        unique=True,
    )
    op.create_index(
        "ix_task_event_executions_status",
        "task_event_executions",
        ["workspace_id", "status", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_event_executions_status",
        table_name="task_event_executions",
    )
    op.drop_index(
        "uq_task_event_executions_agent_queue_item",
        table_name="task_event_executions",
    )
    op.drop_column("task_event_executions", "agent_queue_item_id")
