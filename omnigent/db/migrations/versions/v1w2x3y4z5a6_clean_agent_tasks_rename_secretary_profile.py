"""truncate agent task tables and rename secretary profile column

Revision ID: v1w2x3y4z5a6
Revises: u0v1w2x3y4z5
Create Date: 2026-08-01 08:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v1w2x3y4z5a6"
down_revision: str | None = "u0v1w2x3y4z5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENT_TASK_TABLES = (
    "task_event_executions",
    "task_event_routing_attempts",
    "task_item_events",
    "fyi_cluster_events",
    "task_items",
    "workers",
    "task_assets",
    "task_tags",
    "task_events",
    "fyi_clusters",
    "tasks",
    "user_secretary_profiles",
)


def upgrade() -> None:
    """Drop dummy agent-task rows and align secretary profile naming."""
    conn = op.get_bind()
    for table in _AGENT_TASK_TABLES:
        conn.execute(sa.text(f"DELETE FROM {table}"))

    with op.batch_alter_table("user_secretary_profiles", schema=None) as batch_op:
        batch_op.alter_column("agent_id", new_column_name="agent_profile_id")


def downgrade() -> None:
    with op.batch_alter_table("user_secretary_profiles", schema=None) as batch_op:
        batch_op.alter_column("agent_profile_id", new_column_name="agent_id")
