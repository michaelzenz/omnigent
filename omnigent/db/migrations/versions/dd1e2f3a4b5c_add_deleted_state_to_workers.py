"""Add 'deleted' state to workers CHECK constraint.

Revision ID: dd1e2f3a4b5c
Revises: cc1d2e3f4a5b
"""

from __future__ import annotations

from alembic import op

revision: str = "dd1e2f3a4b5c"
down_revision: str = "cc1d2e3f4a5b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workers DROP CONSTRAINT ck_workers_state"
    )
    op.execute(
        "ALTER TABLE workers ADD CONSTRAINT ck_workers_state "
        "CHECK (state IN ('uninitialized', 'initializing', 'idle', 'busy', "
        "'disconnected', 'initialization_failed', 'terminated', 'deleted'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE workers DROP CONSTRAINT ck_workers_state"
    )
    op.execute(
        "ALTER TABLE workers ADD CONSTRAINT ck_workers_state "
        "CHECK (state IN ('uninitialized', 'initializing', 'idle', 'busy', "
        "'disconnected', 'initialization_failed', 'terminated'))"
    )
