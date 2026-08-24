"""Add rapid-disconnect tracking columns to hosts.

Revision ID: zz3a4b5c6d7e
Revises: zz2q3r4s5t6u
Create Date: 2026-08-25 00:00:00.000000

Adds ``last_connect_at`` (nullable int) and ``consecutive_rapid_disconnects``
(int, default 0) to the ``hosts`` table. Used to detect rapid
connect/disconnect cycles that indicate SSH tunnel thrashing — typically
caused by two server instances competing for the same remote socket.

Additive. No existing data needs backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zz3a4b5c6d7e"
down_revision: str | None = "b4c5d6e7f801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add rapid-disconnect tracking columns to ``hosts``."""
    op.add_column(
        "hosts",
        sa.Column("last_connect_at", sa.Integer(), nullable=True),
    )
    op.add_column(
        "hosts",
        sa.Column(
            "consecutive_rapid_disconnects",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """Remove rapid-disconnect tracking columns from ``hosts``."""
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("consecutive_rapid_disconnects")
        batch_op.drop_column("last_connect_at")
