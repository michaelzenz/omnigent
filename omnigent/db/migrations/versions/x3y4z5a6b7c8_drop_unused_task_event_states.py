"""drop unused task event states and awaiting_user_selection index

Revision ID: x3y4z5a6b7c8
Revises: w2x3y4z5a6b7
Create Date: 2026-08-01 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "x3y4z5a6b7c8"
down_revision: str | None = "w2x3y4z5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the awaiting_user_selection index and tighten the state check.

    Removes the unused ``routing`` (2), ``awaiting_user_selection`` (3), and
    ``awaiting_user_ack`` (10) event states. The prior clean migration truncated
    all agent-task tables, so no rows carry these codes.
    """
    op.drop_index(
        "ix_task_events_awaiting_user_selection",
        table_name="task_events",
    )
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 4, 6, 7, 8, 9, 12)",
        )


def downgrade() -> None:
    """Restore the dropped index and looser state check."""
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 2, 3, 4, 6, 7, 8, 9, 10, 12)",
        )
    op.create_index(
        "ix_task_events_awaiting_user_selection",
        "task_events",
        ["workspace_id", "state", "updated_at", "id"],
        unique=False,
    )
