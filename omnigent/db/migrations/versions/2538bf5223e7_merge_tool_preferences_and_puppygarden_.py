"""Merge tool preferences and puppygarden v2 heads

Revision ID: 2538bf5223e7
Revises: ac1b2c3d4e5f, p2q3r4s5t6u7
Create Date: 2026-08-23 09:19:20.565474
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "2538bf5223e7"
down_revision: str | Sequence[str] | None = ("ac1b2c3d4e5f", "p2q3r4s5t6u7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
