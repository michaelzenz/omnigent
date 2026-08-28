"""add catch_up column to scheduled_tasks

Revision ID: c7d8e9f0a1b2
Revises: d1e2f3a4b5c7
Create Date: 2026-09-01 00:00:00.000000

Adds a ``catch_up`` (BOOLEAN, NOT NULL, default TRUE) column to
``scheduled_tasks``. When True (default), a boot-time catch-up fires once
if a missed occurrence exists. When False, missed occurrences are skipped
on boot.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "d1e2f3a4b5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add catch_up to scheduled_tasks."""
    op.add_column(
        "scheduled_tasks",
        sa.Column(
            "catch_up",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    """Remove catch_up from scheduled_tasks."""
    with op.batch_alter_table("scheduled_tasks") as batch_op:
        batch_op.drop_column("catch_up")
