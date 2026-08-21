"""Add the Auto Include prompt-profile mode."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w4e5f6g7h8i9"
down_revision = "v3d4e5f6g7h8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_constraint("ck_conversations_prompt_profile_mode", type_="check")
        batch_op.alter_column(
            "prompt_profile_mode",
            existing_type=sa.String(length=8),
            type_=sa.String(length=16),
            existing_nullable=True,
        )
        batch_op.create_check_constraint(
            "ck_conversations_prompt_profile_mode",
            "prompt_profile_mode IS NULL OR "
            "prompt_profile_mode IN ('auto', 'auto_include', 'fixed')",
        )
    op.add_column(
        "model_settings",
        sa.Column(
            "prompt_profile_auto_include_limit",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )


def downgrade() -> None:
    op.drop_column("model_settings", "prompt_profile_auto_include_limit")
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_constraint("ck_conversations_prompt_profile_mode", type_="check")
        batch_op.alter_column(
            "prompt_profile_mode",
            existing_type=sa.String(length=16),
            type_=sa.String(length=8),
            existing_nullable=True,
        )
        batch_op.create_check_constraint(
            "ck_conversations_prompt_profile_mode",
            "prompt_profile_mode IS NULL OR prompt_profile_mode IN ('auto', 'fixed')",
        )
