"""Merge scheduled_tasks permission_mode and rapid disconnect tracking heads.

Revision ID: m3rg3p3rm1ss10n_d1sc0nn3ct
Revises: e5d9bc8ac650, zz3a4b5c6d7e
Create Date: 2026-08-25
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "m3rg3p3rm1ss10n_d1sc0nn3ct"
down_revision: Union[str, None] = ("e5d9bc8ac650", "zz3a4b5c6d7e")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
