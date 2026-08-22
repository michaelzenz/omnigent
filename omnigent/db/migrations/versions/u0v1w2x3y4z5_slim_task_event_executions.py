"""drop denormalized columns from task_event_executions

Revision ID: u0v1w2x3y4z5
Revises: t9u0v1w2x3y4
Create Date: 2026-08-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u0v1w2x3y4z5"
down_revision: str | None = "t9u0v1w2x3y4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove redundant execution profile and event linkage columns."""
    op.drop_index("ix_task_event_executions_event_status", table_name="task_event_executions")
    op.drop_index("ix_task_event_executions_worker", table_name="task_event_executions")
    op.drop_index("ix_task_event_executions_event", table_name="task_event_executions")
    with op.batch_alter_table("task_event_executions", schema=None) as batch_op:
        batch_op.drop_column("manager_agent_id")
        batch_op.drop_column("worker_agent_id")
        batch_op.drop_column("event_id")


def downgrade() -> None:
    """Restore denormalized execution columns."""
    from omnigent.db.db_models import Uuid16

    with op.batch_alter_table("task_event_executions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("event_id", Uuid16(), nullable=True))
        batch_op.add_column(sa.Column("worker_agent_id", Uuid16(), nullable=False))
        batch_op.add_column(sa.Column("manager_agent_id", Uuid16(), nullable=False))
    op.create_index(
        "ix_task_event_executions_event",
        "task_event_executions",
        ["workspace_id", "event_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_event_executions_worker",
        "task_event_executions",
        ["workspace_id", "worker_agent_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_event_executions_event_status",
        "task_event_executions",
        ["workspace_id", "event_id", "status", "id"],
        unique=False,
    )
