"""Merge execution-generation and permission/disconnect heads.

Revision ID: n4rg3x3c_g3n_m3rg3
Revises: m3rg3p3rm1ss10n_d1sc0nn3ct, zz4b5c6d7e8f
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "n4rg3x3c_g3n_m3rg3"
down_revision: str | None = ("m3rg3p3rm1ss10n_d1sc0nn3ct", "zz4b5c6d7e8f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
