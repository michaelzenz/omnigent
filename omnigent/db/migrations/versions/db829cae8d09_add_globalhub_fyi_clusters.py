"""add GlobalHub FYI clusters

Revision ID: db829cae8d09
Revises: 777a6c68d35a
Create Date: 2026-11-13 00:00:00.000000

Adds ``fyi_clusters``, groups of events that need no task, and
``task_events.fyi_cluster_id``, the cluster an event was filed into.
Ownership is stamped on the row and every read is owner-scoped. Additive;
there are no foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "db829cae8d09"
down_revision: str | None = "777a6c68d35a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``fyi_clusters`` and add ``task_events.fyi_cluster_id``."""
    op.create_table(
        "fyi_clusters",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("headline", sa.String(512), nullable=False),
        # CompressedText in the model, which stores zstd frames as binary.
        sa.Column("rationale", sa.LargeBinary(), nullable=True),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
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
    op.add_column("task_events", sa.Column("fyi_cluster_id", Uuid16(), nullable=True))
    op.create_index(
        "ix_task_events_fyi_cluster",
        "task_events",
        ["workspace_id", "fyi_cluster_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop ``task_events.fyi_cluster_id`` and ``fyi_clusters``."""
    op.drop_index("ix_task_events_fyi_cluster", table_name="task_events")
    # DROP COLUMN needs batch_alter_table on older SQLite.
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_column("fyi_cluster_id")
    op.drop_index("ix_fyi_clusters_owner_state", table_name="fyi_clusters")
    op.drop_table("fyi_clusters")
