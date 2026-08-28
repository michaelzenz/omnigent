"""add agent task binding and denormalized routing columns

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-21 00:00:00.000000

Adds manager session fields, event ingress provenance, and session-to-task
bindings for direct manager delivery on known sessions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "o2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add binding table and denormalized routing columns."""
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("manager_conversation_id", Uuid16(), nullable=True))

    with op.batch_alter_table("task_events") as batch_op:
        batch_op.add_column(sa.Column("manager_agent_id", Uuid16(), nullable=True))
        batch_op.add_column(sa.Column("manager_conversation_id", Uuid16(), nullable=True))
        batch_op.add_column(sa.Column("source_key", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("source_offset", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("source_session_id", Uuid16(), nullable=True))

    op.create_index(
        "ix_task_events_manager_agent_state",
        "task_events",
        ["workspace_id", "manager_agent_id", "state", "id"],
        unique=False,
    )

    op.create_table(
        "task_session_bindings",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("session_id", Uuid16(), nullable=False),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("manager_agent_id", Uuid16(), nullable=False),
        sa.Column("manager_conversation_id", Uuid16(), nullable=True),
        sa.Column("binding_kind", sa.String(32), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "session_id"),
    )
    op.create_index(
        "ix_task_session_bindings_task_id",
        "task_session_bindings",
        ["workspace_id", "task_id", "session_id"],
        unique=False,
    )

    op.create_index(
        "ix_task_event_executions_conversation_id",
        "task_event_executions",
        ["workspace_id", "conversation_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop binding table and denormalized routing columns."""
    op.drop_index("ix_task_event_executions_conversation_id", table_name="task_event_executions")
    op.drop_index("ix_task_session_bindings_task_id", table_name="task_session_bindings")
    op.drop_table("task_session_bindings")
    op.drop_index("ix_task_events_manager_agent_state", table_name="task_events")

    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_column("source_session_id")
        batch_op.drop_column("source_offset")
        batch_op.drop_column("source_key")
        batch_op.drop_column("manager_conversation_id")
        batch_op.drop_column("manager_agent_id")

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("manager_conversation_id")
