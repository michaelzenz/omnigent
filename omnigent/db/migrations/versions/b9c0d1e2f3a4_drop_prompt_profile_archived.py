"""Drop the archived column from prompt_profiles.

Revision ID: b9c0d1e2f3a4
Revises: zp1q2r3s4t5u
Create Date: 2026-08-20 10:00:00.000000

Prompt profiles now use hard-delete instead of soft-delete tombstones.
This migration purges any rows left in the archived state, drops the
``archived`` column, and rebuilds the active-list index without it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: str | None = "zp1q2r3s4t5u"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM prompt_profiles WHERE archived")

    with op.batch_alter_table("prompt_profiles") as batch_op:
        batch_op.drop_index("ix_prompt_profiles_active")
        batch_op.drop_column("archived")
        batch_op.create_index(
            "ix_prompt_profiles_active",
            ["workspace_id", "enabled", "created_at", "id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("prompt_profiles") as batch_op:
        batch_op.drop_index("ix_prompt_profiles_active")
        batch_op.add_column(
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index(
            "ix_prompt_profiles_active",
            ["workspace_id", "archived", "enabled", "created_at", "id"],
        )
