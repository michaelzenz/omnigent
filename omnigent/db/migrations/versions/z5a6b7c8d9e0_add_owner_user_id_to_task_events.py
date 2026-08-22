"""Add task-event ownership and queue conversation lookup index.

Revision ID: z5a6b7c8d9e0
Revises: y4z5a6b7c8d9
Create Date: 2026-08-01

The secretary packager polls ``awaiting_grouping`` events and groups them by
owner to build per-user batches. Until now the owner was only known in the
distributor's request context and never persisted on the event row, so a
poll-only packager could not group. This adds a nullable ``owner_user_id``
column, set at event creation (the route knows the caller) and as a fallback
at stall time.

The queue status feed resolves a queue from a session id on every terminal
session-status edge, so index that lookup by workspace and conversation.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "z5a6b7c8d9e0"
down_revision: str | None = "y4z5a6b7c8d9"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("owner_user_id", sa.String(length=128), nullable=True),
        )
    op.create_index(
        "ix_agent_queues_conversation_id",
        "agent_queues",
        ["workspace_id", "conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_queues_conversation_id",
        table_name="agent_queues",
    )
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_column("owner_user_id")
