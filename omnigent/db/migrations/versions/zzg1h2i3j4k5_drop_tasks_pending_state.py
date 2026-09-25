"""Drop the tasks pending state (2): rewrite rows to active.

The born-pending task-package confirm flow is removed; tasks are born
active. Legacy pending rows enter the live queue as active, and the
state CHECK shrinks accordingly.

Revision ID: zzg1h2i3j4k5
Revises: zzb7c8d9e0f1
"""

from __future__ import annotations

from alembic import op

revision: str = "zzg1h2i3j4k5"
down_revision: str | None = "zzb7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE tasks SET state = 1 WHERE state = 2")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_state")
    op.execute("ALTER TABLE tasks ADD CONSTRAINT ck_tasks_state CHECK (state IN (1, 3, 4, 5))")


def downgrade() -> None:
    # Rows already rewritten to active cannot be mapped back to pending.
    op.execute("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_state")
    op.execute("ALTER TABLE tasks ADD CONSTRAINT ck_tasks_state CHECK (state IN (1, 2, 3, 4, 5))")
