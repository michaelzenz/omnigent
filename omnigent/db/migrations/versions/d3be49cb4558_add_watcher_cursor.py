"""Add the cursor column to GlobalHub watchers.

Revision ID: d3be49cb4558
Revises: c2ed8e489ae1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3be49cb4558"
down_revision: str | None = "c2ed8e489ae1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("watchers", sa.Column("cursor", sa.String(100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("watchers") as batch_op:
        batch_op.drop_column("cursor")
