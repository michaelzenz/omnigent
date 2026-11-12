"""add the GlobalHub managers table

Revision ID: 1a547c56498a
Revises: bde2d48f67a1
Create Date: 2026-11-12 00:00:00.000000

Adds ``managers``: the durable identity behind each GlobalHub manager session.
Ownership is stamped on the row and every read is owner-scoped. Additive; there
are no foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "1a547c56498a"
down_revision: str | None = "bde2d48f67a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``managers`` table."""
    op.create_table(
        "managers",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=True),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("host_id", Uuid16(), nullable=True),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_managers_owner",
        "managers",
        ["workspace_id", "owner_user_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_managers_conversation",
        "managers",
        ["workspace_id", "conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the ``managers`` table."""
    op.drop_index("ix_managers_conversation", table_name="managers")
    op.drop_index("ix_managers_owner", table_name="managers")
    op.drop_table("managers")
