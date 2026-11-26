"""Add the problem column to GlobalHub watchers.

Revision ID: e4b19c7d2a63
Revises: 52efa641b592

``watchers.problem`` holds JSON of what stopped a watcher's latest run from
reading its sources. The board shows it until a later run works.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b19c7d2a63"
down_revision: str | None = "52efa641b592"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("watchers", sa.Column("problem", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("watchers") as batch_op:
        batch_op.drop_column("problem")
