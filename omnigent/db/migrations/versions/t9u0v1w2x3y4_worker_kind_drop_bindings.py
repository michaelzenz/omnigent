"""workers.kind, session indexes; drop task_session_bindings

Revision ID: t9u0v1w2x3y4
Revises: s8t9u0v1w2x3
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t9u0v1w2x3y4"
down_revision: str | None = "s8t9u0v1w2x3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add worker kind and session lookup indexes; remove session bindings."""
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="managed"),
        )
        batch_op.create_check_constraint(
            "ck_workers_kind",
            "kind IN ('managed', 'external')",
        )
    op.create_index(
        "ix_workers_session",
        "workers",
        ["workspace_id", "session_id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_manager_conversation",
        "tasks",
        ["workspace_id", "manager_conversation_id"],
        unique=False,
    )
    op.drop_index("ix_task_session_bindings_task_id", table_name="task_session_bindings")
    op.drop_table("task_session_bindings")


def downgrade() -> None:
    """Restore task_session_bindings and drop worker kind."""
    from omnigent.db.db_models import Uuid16

    op.create_table(
        "task_session_bindings",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("session_id", Uuid16(), nullable=False),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("manager_agent_id", Uuid16(), nullable=False),
        sa.Column("manager_conversation_id", Uuid16(), nullable=True),
        sa.Column("binding_kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "session_id"),
    )
    op.create_index(
        "ix_task_session_bindings_task_id",
        "task_session_bindings",
        ["workspace_id", "task_id", "session_id"],
    )
    op.drop_index("ix_tasks_manager_conversation", table_name="tasks")
    op.drop_index("ix_workers_session", table_name="workers")
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.drop_constraint("ck_workers_kind", type_="check")
        batch_op.drop_column("kind")
