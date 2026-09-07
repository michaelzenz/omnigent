"""Merge ssh settings per-user and worker title heads.

Revision ID: zzd3e4f5a6b7
Revises: zzc2d3e4f5a6, zzb1c2d3e4f5
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "zzd3e4f5a6b7"
down_revision: str | Sequence[str] | None = ("zzc2d3e4f5a6", "zzb1c2d3e4f5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
