"""Persist per-host skill synchronization configuration.

Revision ID: j0a1b2c3d4e5
Revises: i0a1b2c3d4e5
Create Date: 2026-08-17 22:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j0a1b2c3d4e5"
down_revision: str | None = "i0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.add_column(sa.Column("skill_sync_harnesses", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("skill_search_roots", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("skill_search_roots")
        batch_op.drop_column("skill_sync_harnesses")
