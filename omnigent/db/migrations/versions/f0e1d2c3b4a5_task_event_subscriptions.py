"""Add task event subscriptions and broadcast fan-out state.

Ingress events keep one canonical row (``parent_event_id`` NULL); a
subscription match fans out per-task copies and the canonical row settles in
the new ``broadcast`` state.

Revision ID: f0e1d2c3b4a5
Revises: c7d8e9f0a1b2
Create Date: 2026-08-04 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision = "f0e1d2c3b4a5"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.add_column(sa.Column("parent_event_id", Uuid16(), nullable=True))
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 4, 6, 7, 8, 9, 12, 13)",
        )

    op.create_table(
        "task_event_subscriptions",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id",
            "task_id",
            "source",
            "source_key",
            name="uq_task_event_subscriptions_task_source",
        ),
    )
    op.create_index(
        "ix_task_event_subscriptions_source",
        "task_event_subscriptions",
        ["workspace_id", "source", "source_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_event_subscriptions_source", table_name="task_event_subscriptions"
    )
    op.drop_table("task_event_subscriptions")

    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 4, 6, 7, 8, 9, 12)",
        )
        batch_op.drop_column("parent_event_id")
