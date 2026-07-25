"""add fyi clusters and classified_fyi event state

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-07-25 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a8"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add FYI cluster tables and classified_fyi task-event state."""
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)",
        )

    op.create_table(
        "fyi_clusters",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("canonical_key", sa.String(length=256), nullable=True),
        sa.Column("headline", sa.String(length=512), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("state", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("resolved_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.CheckConstraint("state IN (1, 2)", name="ck_fyi_clusters_state"),
    )
    op.create_index(
        "ix_fyi_clusters_owner_state",
        "fyi_clusters",
        ["workspace_id", "owner_user_id", "state", "id"],
        unique=False,
    )
    op.create_index(
        "ix_fyi_clusters_canonical",
        "fyi_clusters",
        ["workspace_id", "canonical_key", "state"],
        unique=False,
    )

    op.create_table(
        "fyi_cluster_events",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cluster_id", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "cluster_id", "event_id"),
    )


def downgrade() -> None:
    """Revert FYI cluster schema changes."""
    op.drop_table("fyi_cluster_events")
    op.drop_index("ix_fyi_clusters_canonical", table_name="fyi_clusters")
    op.drop_index("ix_fyi_clusters_owner_state", table_name="fyi_clusters")
    op.drop_table("fyi_clusters")

    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_events_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_events_state",
            "state IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)",
        )
