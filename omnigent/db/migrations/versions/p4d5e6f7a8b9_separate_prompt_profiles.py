"""Separate prompt profiles from execution-target agents.

Revision ID: p4d5e6f7a8b9
Revises: o3c4d5e6f7a8
Create Date: 2026-08-19 11:55:00.000000

This expand step intentionally performs no data migration. Operators must stop
the application, upgrade to this revision, migrate profile rows and session
selections manually, then apply the following contract revision before
deploying the new application.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "p4d5e6f7a8b9"
down_revision: str | None = "o3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_profiles",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.LargeBinary(), nullable=True),
        sa.Column("instructions", sa.LargeBinary(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_prompt_profiles"),
    )
    op.create_index(
        "ix_prompt_profiles_name",
        "prompt_profiles",
        ["workspace_id", "name", "id"],
    )
    op.create_index(
        "ix_prompt_profiles_active",
        "prompt_profiles",
        ["workspace_id", "archived", "enabled", "created_at", "id"],
    )
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("prompt_profile_mode", sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column("prompt_profile_id", Uuid16(), nullable=True))
        batch_op.create_check_constraint(
            "ck_conversations_prompt_profile_mode",
            "prompt_profile_mode IS NULL OR prompt_profile_mode IN ('auto', 'fixed')",
        )
def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_constraint("ck_conversations_prompt_profile_mode", type_="check")
        batch_op.drop_column("prompt_profile_id")
        batch_op.drop_column("prompt_profile_mode")
    op.drop_index("ix_prompt_profiles_active", table_name="prompt_profiles")
    op.drop_index("ix_prompt_profiles_name", table_name="prompt_profiles")
    op.drop_table("prompt_profiles")
