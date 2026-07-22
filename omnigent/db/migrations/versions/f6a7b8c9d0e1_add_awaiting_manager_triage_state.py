"""add awaiting_manager_triage task event state

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extend task event states for manager triage after routing."""
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)",
        )


def downgrade() -> None:
    """Revert the task event state constraint."""
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 2, 3, 4, 5, 6, 7, 8, 9)",
        )
