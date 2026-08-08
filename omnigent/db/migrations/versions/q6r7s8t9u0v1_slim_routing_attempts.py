"""slim routing attempts and drop resolution tables

Revision ID: q6r7s8t9u0v1
Revises: p5q6r7s8t9u0
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q6r7s8t9u0v1"
down_revision: str | None = "p5q6r7s8t9u0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_task_event_routing_resolutions_event", table_name="task_event_routing_resolutions"
    )
    op.drop_table("task_event_routing_resolutions")

    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_column("selected_routing_attempt_id")

    op.drop_index(
        "ix_task_event_routing_attempts_event_decision",
        table_name="task_event_routing_attempts",
    )
    with op.batch_alter_table("task_event_routing_attempts", schema=None) as batch_op:
        batch_op.drop_constraint(
            "ck_task_event_routing_attempts_decision",
            type_="check",
        )
        batch_op.drop_column("candidate_manager_agent_id")
        batch_op.drop_column("rank")
        batch_op.drop_column("decision")
        batch_op.drop_column("responded_at")
        batch_op.drop_column("selected_at")
        batch_op.alter_column("manager_reason", new_column_name="reason")


def downgrade() -> None:
    from omnigent.db.db_models import Uuid16

    with op.batch_alter_table("task_event_routing_attempts", schema=None) as batch_op:
        batch_op.alter_column("reason", new_column_name="manager_reason")
        batch_op.add_column(sa.Column("selected_at", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("responded_at", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("decision", sa.SmallInteger(), nullable=False, server_default="1"),
        )
        batch_op.add_column(
            sa.Column("rank", sa.SmallInteger(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("candidate_manager_agent_id", Uuid16(), nullable=False))
        batch_op.create_check_constraint(
            "ck_task_event_routing_attempts_decision",
            "decision IN (1, 2, 3, 4, 5)",
        )

    op.create_index(
        "ix_task_event_routing_attempts_event_decision",
        "task_event_routing_attempts",
        ["workspace_id", "event_id", "decision", "id"],
        unique=False,
    )

    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("selected_routing_attempt_id", Uuid16(), nullable=True))

    op.create_table(
        "task_event_routing_resolutions",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("event_id", Uuid16(), nullable=False),
        sa.Column("selected_attempt_id", Uuid16(), nullable=False),
        sa.Column("selected_task_id", Uuid16(), nullable=False),
        sa.Column("selected_manager_agent_id", Uuid16(), nullable=False),
        sa.Column("resolved_by_user_id", sa.String(length=128), nullable=True),
        sa.Column("resolution_note", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_task_event_routing_resolutions_event",
        "task_event_routing_resolutions",
        ["workspace_id", "event_id", "id"],
        unique=False,
    )
