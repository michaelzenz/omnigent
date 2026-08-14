"""make user_task_role_profiles.model nullable

Revision ID: a7c3e91d5b28
Revises: e0f1a2b3c4d5
Create Date: 2026-08-07 17:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e91d5b28"
down_revision: str | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_task_role_profiles", schema=None) as batch_op:
        batch_op.alter_column(
            "model",
            existing_type=sa.String(length=128),
            nullable=True,
        )
    # Harnesses that pick their model inside the vendor TUI carry no Omnigent
    # model. Rows predating that rule kept the Cursor default, which reached
    # the CLI as an unroutable ``--model``.
    op.execute(
        sa.text(
            "UPDATE user_task_role_profiles SET model = NULL "
            "WHERE model LIKE 'composer-%' AND harness NOT LIKE '%cursor%'"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE user_task_role_profiles SET model = '' WHERE model IS NULL"))
    with op.batch_alter_table("user_task_role_profiles", schema=None) as batch_op:
        batch_op.alter_column(
            "model",
            existing_type=sa.String(length=128),
            nullable=False,
        )
