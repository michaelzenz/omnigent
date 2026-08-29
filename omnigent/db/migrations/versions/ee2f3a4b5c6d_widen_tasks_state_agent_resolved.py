"""Widen tasks.state CHECK to accept agent-resolved (5).

Revision ID: ee2f3a4b5c6d
Revises: dd1e2f3a4b5c
"""

from __future__ import annotations

from alembic import op

revision: str = "ee2f3a4b5c6d"
down_revision: str = "dd1e2f3a4b5c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_state")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_state "
        "CHECK (state IN (1, 2, 3, 4, 5))"
    )


def downgrade() -> None:
    # Fails if any row is already in state 5.
    op.execute("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_state")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_state "
        "CHECK (state IN (1, 2, 3, 4))"
    )
