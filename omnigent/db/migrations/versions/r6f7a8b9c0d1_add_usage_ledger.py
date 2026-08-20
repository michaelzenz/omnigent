"""Add Omnigent request usage ledger and workload classification setting.

Revision ID: r6f7a8b9c0d1
Revises: q5e6f7a8b9c0
Create Date: 2026-08-19 18:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import BINARY as MySQLBinary

revision: str = "r6f7a8b9c0d1"
down_revision: str | None = "q5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_UUID16 = sa.LargeBinary(length=16).with_variant(MySQLBinary(16), "mysql")


def upgrade() -> None:
    with op.batch_alter_table("model_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "workload_classification_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    op.create_table(
        "usage_ledger",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("id", _UUID16, nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.Integer(), nullable=False),
        sa.Column("day_utc", sa.String(length=10), nullable=False),
        sa.Column("session_id", _UUID16, nullable=True),
        sa.Column("turn_id", sa.String(length=64), nullable=True),
        sa.Column("purpose", sa.String(length=160), nullable=False),
        sa.Column("model", sa.String(length=300), nullable=True),
        sa.Column("workload", sa.String(length=32), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cache_read_input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "cache_creation_input_tokens",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("input_price_per_token", sa.Float(), nullable=True),
        sa.Column("output_price_per_token", sa.Float(), nullable=True),
        sa.Column("cache_read_price_per_token", sa.Float(), nullable=True),
        sa.Column("cache_write_price_per_token", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("priced", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_usage_ledger_user_month",
        "usage_ledger",
        ["workspace_id", "user_id", "day_utc"],
    )
    op.create_index(
        "ix_usage_ledger_session",
        "usage_ledger",
        ["workspace_id", "session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_ledger_session", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_user_month", table_name="usage_ledger")
    op.drop_table("usage_ledger")
    with op.batch_alter_table("model_settings") as batch_op:
        batch_op.drop_column("workload_classification_enabled")
