"""Add user model pricing overrides and ledger pricing source.

Revision ID: s7g8h9i0j1k2
Revises: r6f7a8b9c0d1
Create Date: 2026-08-19 19:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s7g8h9i0j1k2"
down_revision: str | None = "r6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_pricing_overrides",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=300), nullable=False),
        sa.Column("input_price_per_token", sa.Float(), nullable=False),
        sa.Column("output_price_per_token", sa.Float(), nullable=False),
        sa.Column("cache_read_price_per_token", sa.Float(), nullable=True),
        sa.Column("cache_write_price_per_token", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "input_price_per_token >= 0",
            name="ck_model_pricing_overrides_input_nonnegative",
        ),
        sa.CheckConstraint(
            "output_price_per_token >= 0",
            name="ck_model_pricing_overrides_output_nonnegative",
        ),
        sa.CheckConstraint(
            "cache_read_price_per_token IS NULL OR cache_read_price_per_token >= 0",
            name="ck_model_pricing_overrides_cache_read_nonnegative",
        ),
        sa.CheckConstraint(
            "cache_write_price_per_token IS NULL OR cache_write_price_per_token >= 0",
            name="ck_model_pricing_overrides_cache_write_nonnegative",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", "model"),
    )
    with op.batch_alter_table("usage_ledger") as batch_op:
        batch_op.add_column(sa.Column("pricing_source", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usage_ledger") as batch_op:
        batch_op.drop_column("pricing_source")
    op.drop_table("model_pricing_overrides")
