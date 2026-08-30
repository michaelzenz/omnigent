"""Add dispatch_stoplist table.

Revision ID: b6d7e8f9a0c1
Revises: ee2f3a4b5c6d
Create Date: 2026-08-29

Global dispatcher stoplist: one row per role the user has told the
dispatcher not to dispatch. The PuppyGarden board config panel toggles
roles (currently only the broker) through
``PUT /v1/agent-queues/dispatch-stoplist``; the dispatcher filters
stopped roles out of every scan pass. Distinct from a per-queue
``paused`` state: this is role-wide and survives restarts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b6d7e8f9a0c1"
down_revision: str | None = "ee2f3a4b5c6d"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Create the dispatch_stoplist table."""
    op.create_table(
        "dispatch_stoplist",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "role"),
    )


def downgrade() -> None:
    """Drop the dispatch_stoplist table."""
    op.drop_table("dispatch_stoplist")
