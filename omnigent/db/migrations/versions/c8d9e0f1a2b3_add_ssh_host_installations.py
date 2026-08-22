"""add durable SSH host installation state

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-06 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the SSH host reconciliation table."""
    op.create_table(
        "ssh_host_installations",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("connection_id", sa.String(length=64), nullable=False),
        sa.Column("ssh_alias", sa.String(length=128), nullable=False),
        sa.Column("host_id", Uuid16(), nullable=False),
        sa.Column("owner", sa.String(length=256), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("bundle_version", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "desired_state IN ('connected', 'detached')",
            name="ck_ssh_host_installations_desired_state",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "connection_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "host_id",
            name="uq_ssh_host_installations_host_id",
        ),
    )
    op.create_index(
        "ix_ssh_host_installations_due",
        "ssh_host_installations",
        ["workspace_id", "desired_state", "next_attempt_at", "lease_expires_at"],
    )


def downgrade() -> None:
    """Drop the SSH host reconciliation table."""
    op.drop_index("ix_ssh_host_installations_due", table_name="ssh_host_installations")
    op.drop_table("ssh_host_installations")
