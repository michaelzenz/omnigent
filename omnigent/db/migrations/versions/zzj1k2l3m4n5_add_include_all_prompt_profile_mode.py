"""Add the Include All prompt-profile mode."""

from __future__ import annotations

from alembic import op

revision = "zzj1k2l3m4n5"
down_revision = "zzh2i3j4k5l6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_constraint("ck_conversations_prompt_profile_mode", type_="check")
        batch_op.create_check_constraint(
            "ck_conversations_prompt_profile_mode",
            "prompt_profile_mode IS NULL OR "
            "prompt_profile_mode IN ('auto', 'auto_include', 'fixed', 'include_all')",
        )


def downgrade() -> None:
    # include_all does not exist before this revision; map those rows to the
    # closest LLM-filtered mode so they satisfy the older constraint.
    op.execute(
        "UPDATE conversations SET prompt_profile_mode = 'auto_include' "
        "WHERE prompt_profile_mode = 'include_all'"
    )
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_constraint("ck_conversations_prompt_profile_mode", type_="check")
        batch_op.create_check_constraint(
            "ck_conversations_prompt_profile_mode",
            "prompt_profile_mode IS NULL OR "
            "prompt_profile_mode IN ('auto', 'auto_include', 'fixed')",
        )
