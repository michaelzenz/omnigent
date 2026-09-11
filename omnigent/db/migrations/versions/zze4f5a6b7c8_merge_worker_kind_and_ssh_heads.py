"""Merge worker-kind and SSH migration heads.

Revision ID: zze4f5a6b7c8
Revises: zzc1d2d3e4f5, zzd3e4f5a6b7
Create Date: 2026-09-08 13:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "zze4f5a6b7c8"
down_revision: str | Sequence[str] | None = ("zzc1d2d3e4f5", "zzd3e4f5a6b7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge the concurrent migration branches."""


def downgrade() -> None:
    """Split back to the two parent heads."""
