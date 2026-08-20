"""Add the user-selected global memory provider.

Revision ID: t1b2c3d4e5f6
Revises: s7g8h9i0j1k2
Create Date: 2026-08-19 23:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t1b2c3d4e5f6"
down_revision: str | None = "s7g8h9i0j1k2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_settings",
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default="omniharness",
        ),
    )


def downgrade() -> None:
    op.drop_column("memory_settings", "provider")
