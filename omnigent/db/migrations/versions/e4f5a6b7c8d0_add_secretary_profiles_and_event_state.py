"""add secretary profiles and awaiting_new_manager_decision event state

Revision ID: e4f5a6b7c8d0
Revises: d3e4f5a6b7c8
Create Date: 2026-07-21 00:00:00.000000

Adds per-user secretary configuration/session binding and extends task event
states for the no-manager-accept escalation path.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "e4f5a6b7c8d0"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add secretary profiles and the new task-event stall state."""
    op.create_table(
        "user_secretary_profiles",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("agent_id", Uuid16(), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=True),
        sa.Column("harness", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("host_id", sa.String(64), nullable=True),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_index(
        "ix_user_secretary_profiles_conversation",
        "user_secretary_profiles",
        ["workspace_id", "conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop secretary profiles."""
    op.drop_index("ix_user_secretary_profiles_conversation", table_name="user_secretary_profiles")
    op.drop_table("user_secretary_profiles")
