"""rename secretary profiles to task role profiles

Revision ID: w2x3y4z5a6b7
Revises: v1w2x3y4z5a6
Create Date: 2026-08-01 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "w2x3y4z5a6b7"
down_revision: str | None = "v1w2x3y4z5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace the secretary profile table with a role-general one.

    The previous migration truncated all agent-task tables, so
    ``user_secretary_profiles`` is empty here; the table is dropped and
    recreated with the role column as part of the primary key.
    """
    op.drop_index(
        "ix_user_secretary_profiles_conversation",
        table_name="user_secretary_profiles",
    )
    op.drop_table("user_secretary_profiles")

    op.create_table(
        "user_task_role_profiles",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("agent_profile_id", Uuid16(), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=True),
        sa.Column("harness", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("host_id", sa.String(64), nullable=True),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", "role"),
    )
    op.create_index(
        "ix_user_task_role_profiles_conversation",
        "user_task_role_profiles",
        ["workspace_id", "conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    """Restore the secretary-only profile table."""
    op.drop_index(
        "ix_user_task_role_profiles_conversation",
        table_name="user_task_role_profiles",
    )
    op.drop_table("user_task_role_profiles")

    op.create_table(
        "user_secretary_profiles",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("agent_profile_id", Uuid16(), nullable=False),
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
