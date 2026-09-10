"""Tasks: add resolved_at for auto-archiving agent-resolved tasks.

Revision ID: zzf6a7b8c9d0
Revises: zzd1d2d3e4f5
Create Date: 2026-09-07 00:00:00.000000

Adds ``tasks.resolved_at`` (unix epoch seconds, NULL unless the task is in
``agent-resolved`` state). Backfills currently-resolved tasks from their
``updated_at`` — the state transition bumps it, so it is the best available
estimate of when the resolution happened. The background GC archives
agent-resolved tasks whose ``resolved_at`` is older than the configured
retention (default 1 week).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "zzf6a7b8c9d0"
down_revision: str | None = "zzd1d2d3e4f5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("resolved_at", sa.Integer(), nullable=True))
    # Best-effort backfill: agent-resolved tasks were resolved sometime before
    # their last update. store.update() maintains the column going forward.
    op.execute(
        "UPDATE tasks SET resolved_at = updated_at "
        "WHERE state = 5 AND resolved_at IS NULL AND updated_at IS NOT NULL"
    )
    op.create_index(
        "ix_tasks_resolved_at",
        "tasks",
        ["workspace_id", "state", "resolved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_resolved_at", table_name="tasks")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("resolved_at")
