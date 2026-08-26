"""merge threaded comments and puppygarden heads

Revision ID: 35394a7d04d9
Revises: 2538bf5223e7, ad2c3d4e5f60
Create Date: 2026-08-23 11:53:34.568067
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "35394a7d04d9"
down_revision: str | None = ("2538bf5223e7", "ad2c3d4e5f60")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
