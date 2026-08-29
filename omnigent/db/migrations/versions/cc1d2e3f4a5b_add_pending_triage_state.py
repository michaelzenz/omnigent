"""Add pending_triage state to task_events CHECK constraint.

Revision ID: cc1d2e3f4a5b
Revises: bb1c2d3e4f5a
"""

from __future__ import annotations

from alembic import op

revision: str = "cc1d2e3f4a5b"
down_revision: str = "bb1c2d3e4f5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE task_events DROP CONSTRAINT ck_task_events_state"
    )
    op.execute(
        "ALTER TABLE task_events ADD CONSTRAINT ck_task_events_state "
        "CHECK (state IN (1, 4, 6, 7, 8, 9, 12, 13, 14))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE task_events DROP CONSTRAINT ck_task_events_state"
    )
    op.execute(
        "ALTER TABLE task_events ADD CONSTRAINT ck_task_events_state "
        "CHECK (state IN (1, 4, 6, 7, 8, 9, 12, 13))"
    )
