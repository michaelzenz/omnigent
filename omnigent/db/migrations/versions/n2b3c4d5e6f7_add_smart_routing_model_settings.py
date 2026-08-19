"""Add deployment-wide smart routing settings.

Revision ID: n2b3c4d5e6f7
Revises: m1b2c3d4e5f6
Create Date: 2026-08-19 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n2b3c4d5e6f7"
down_revision: str | None = "m1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "smart_routing_decision_model",
                sa.String(length=300),
                nullable=True,
                server_default="databricks-gpt-5-6-luna",
            )
        )
        batch_op.add_column(
            sa.Column(
                "smart_routing_prompt",
                sa.Text(),
                nullable=True,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column(
                "smart_routing_cadence",
                sa.String(length=32),
                nullable=False,
                server_default="per_turn",
            )
        )
        batch_op.create_check_constraint(
            "ck_model_settings_smart_routing_cadence",
            "smart_routing_cadence IN ('per_turn', 'first_turn_only')",
        )


def downgrade() -> None:
    with op.batch_alter_table("model_settings") as batch_op:
        batch_op.drop_constraint(
            "ck_model_settings_smart_routing_cadence",
            type_="check",
        )
        batch_op.drop_column("smart_routing_cadence")
        batch_op.drop_column("smart_routing_prompt")
        batch_op.drop_column("smart_routing_decision_model")
