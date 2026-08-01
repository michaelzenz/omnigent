"""rename tasks.manager_agent_id to agent_profile_id

Revision ID: n3o4p5q6r7s8
Revises: m2n3o4p5q6r7
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "n3o4p5q6r7s8"
down_revision: str | None = "m2n3o4p5q6r7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_tasks_manager_agent_id", table_name="tasks")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.alter_column("manager_agent_id", new_column_name="agent_profile_id")
    op.create_index(
        "ix_tasks_agent_profile_id",
        "tasks",
        ["workspace_id", "agent_profile_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_agent_profile_id", table_name="tasks")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.alter_column("agent_profile_id", new_column_name="manager_agent_id")
    op.create_index(
        "ix_tasks_manager_agent_id",
        "tasks",
        ["workspace_id", "manager_agent_id", "id"],
        unique=False,
    )
