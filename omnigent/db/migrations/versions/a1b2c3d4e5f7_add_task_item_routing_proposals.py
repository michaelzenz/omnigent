"""add task item routing proposal state and payload column

Revision ID: a1b2c3d4e5f7
Revises: f6a7b8c9d0e1
Create Date: 2026-07-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extend task item/event states and add routing_proposal JSON."""
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)",
        )

    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("routing_proposal", sa.Text(), nullable=True))
        batch_op.drop_constraint("ck_task_items_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_items_state",
            "state IN (1, 2, 3, 4, 5, 6, 7, 8)",
        )

    op.create_index(
        "ix_task_items_routing_canonical",
        "task_items",
        ["workspace_id", "canonical_key", "state"],
        unique=False,
    )


def downgrade() -> None:
    """Revert routing proposal schema changes."""
    op.drop_index("ix_task_items_routing_canonical", table_name="task_items")

    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_items_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_items_state",
            "state IN (1, 2, 3, 4, 5, 6, 7)",
        )
        batch_op.drop_column("routing_proposal")

    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)",
        )
