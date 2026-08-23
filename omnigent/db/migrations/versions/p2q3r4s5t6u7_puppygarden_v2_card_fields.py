"""Add PuppyGarden V2 task ordering, priority, and asset category.

Revision ID: p2q3r4s5t6u7
Revises: c0d1e2f3a4b5
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p2q3r4s5t6u7"
down_revision: str | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(
            sa.Column("priority", sa.Integer(), nullable=False, server_default="2")
        )
        batch_op.add_column(sa.Column("queue_rank", sa.BigInteger(), nullable=True))
        batch_op.create_check_constraint(
            "ck_tasks_priority",
            "priority BETWEEN 0 AND 3",
        )

    # Preserve the old board's update-recency ordering for existing rows.
    op.execute(
        sa.text(
            "UPDATE tasks SET queue_rank = COALESCE(updated_at, created_at, 0) "
            "WHERE queue_rank IS NULL"
        )
    )
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column(
            "queue_rank",
            existing_type=sa.BigInteger(),
            nullable=False,
            server_default="0",
        )
        batch_op.create_index(
            "ix_tasks_queue_rank",
            ["workspace_id", "state", "queue_rank", "id"],
        )

    with op.batch_alter_table("agent_queues") as batch_op:
        batch_op.add_column(
            sa.Column("inspection_hold_token", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("inspection_hold_expires_at", sa.Integer(), nullable=True))

    with op.batch_alter_table("agent_queue_items") as batch_op:
        batch_op.add_column(sa.Column("edit_lease_token", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("edit_lease_expires_at", sa.Integer(), nullable=True))

    with op.batch_alter_table("task_assets") as batch_op:
        batch_op.add_column(
            sa.Column(
                "category",
                sa.String(length=32),
                nullable=False,
                server_default="other",
            )
        )
        batch_op.create_check_constraint(
            "ck_task_assets_category",
            "category IN ('code', 'tests', 'documents', 'logs', 'other')",
        )


def downgrade() -> None:
    with op.batch_alter_table("task_assets") as batch_op:
        batch_op.drop_constraint("ck_task_assets_category", type_="check")
        batch_op.drop_column("category")

    with op.batch_alter_table("agent_queue_items") as batch_op:
        batch_op.drop_column("edit_lease_expires_at")
        batch_op.drop_column("edit_lease_token")

    with op.batch_alter_table("agent_queues") as batch_op:
        batch_op.drop_column("inspection_hold_expires_at")
        batch_op.drop_column("inspection_hold_token")

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_index("ix_tasks_queue_rank")
        batch_op.drop_constraint("ck_tasks_priority", type_="check")
        batch_op.drop_column("queue_rank")
        batch_op.drop_column("priority")
