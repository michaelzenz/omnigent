"""add the GlobalHub watchers table

Revision ID: 57e5a00c4b94
Revises: 62d77984d129
Create Date: 2026-11-15 00:00:00.000000

Adds ``watchers``: the durable identity behind each watcher session, with its
name, description, manual and model. A watcher's schedule is the
``role_schedules`` row keyed ``"watcher:<id>"``. Ownership is stamped on the
row; there are no foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "57e5a00c4b94"
down_revision: str | None = "62d77984d129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``watchers`` and its indexes."""
    op.create_table(
        "watchers",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL before the watcher's first run.
        sa.Column("conversation_id", Uuid16(), nullable=True),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        # NULL uses the packaged watcher template.
        sa.Column("manual", sa.Text(), nullable=True),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_watchers_owner",
        "watchers",
        ["workspace_id", "owner_user_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_watchers_conversation",
        "watchers",
        ["workspace_id", "conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop ``watchers`` and the schedules of its watchers."""
    op.execute(sa.text("DELETE FROM role_schedules WHERE role_key LIKE 'watcher:%'"))
    op.drop_index("ix_watchers_conversation", table_name="watchers")
    op.drop_index("ix_watchers_owner", table_name="watchers")
    op.drop_table("watchers")
