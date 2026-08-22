"""Merge the upstream and PuppyGarden migration heads.

Revision ID: zz2q3r4s5t6u
Revises: za1b2c3d4e5f, zp1q2r3s4t5u
Create Date: 2026-08-22 09:22:00.000000
"""

from __future__ import annotations

revision: str = "zz2q3r4s5t6u"
down_revision: tuple[str, str] = ("za1b2c3d4e5f", "zp1q2r3s4t5u")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
