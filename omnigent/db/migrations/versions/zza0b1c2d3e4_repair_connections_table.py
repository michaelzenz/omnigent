"""Repair databases missing the connections table.

Revision ID: zza0b1c2d3e4
Revises: zz9f0a1b2c3d4
Create Date: 2026-09-06 12:15:00.000000

The connections migration was retroactively inserted before an already
released revision. Databases already stamped at that revision skipped its
table creation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zza0b1c2d3e4"
down_revision: str | None = "zz9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the connections table when the original migration was skipped."""
    if "connections" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "connections",
        sa.Column(
            "workspace_id",
            sa.BigInteger,
            primary_key=True,
            nullable=False,
            server_default="0",
        ),
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(128),
            primary_key=True,
            nullable=False,
            server_default="",
        ),
        sa.Column("secret_enc", sa.Text, nullable=False),
        sa.Column("metadata_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    """Keep the table owned by the original migration."""
