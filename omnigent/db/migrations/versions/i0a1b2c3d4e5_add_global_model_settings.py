"""Add deployment-wide model settings.

Revision ID: i0a1b2c3d4e5
Revises: h0a1b2c3d4e5
Create Date: 2026-08-17 09:15:00.000000
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i0a1b2c3d4e5"
down_revision: str | None = "h0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_HARNESS_MODELS = {
    "omniharness": [
        "databricks-gpt-5-6-luna",
        "databricks-glm-5-2",
        "databricks-kimi-k3",
    ]
}


def upgrade() -> None:
    op.create_table(
        "model_settings",
        sa.Column("id", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("harness_models", sa.Text(), nullable=False),
        sa.Column("policy_model", sa.String(length=300), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_model_settings_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO model_settings "
            "(id, harness_models, policy_model, updated_at, updated_by) "
            "VALUES (1, :harness_models, NULL, NULL, NULL)"
        ).bindparams(harness_models=json.dumps(_DEFAULT_HARNESS_MODELS, separators=(",", ":")))
    )


def downgrade() -> None:
    op.drop_table("model_settings")
