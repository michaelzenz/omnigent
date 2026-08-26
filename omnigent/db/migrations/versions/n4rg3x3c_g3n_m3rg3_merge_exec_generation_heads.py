"""Merge execution-generation and permission/disconnect heads.

Revision ID: n4rg3x3c_g3n_m3rg3
Revises: m3rg3p3rm1ss10n_d1sc0nn3ct, zz4b5c6d7e8f
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "n4rg3x3c_g3n_m3rg3"
down_revision: Union[str, None] = ("m3rg3p3rm1ss10n_d1sc0nn3ct", "zz4b5c6d7e8f")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
