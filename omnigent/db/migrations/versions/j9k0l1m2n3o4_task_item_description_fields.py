"""task item description and internal_note; drop model/harness

Revision ID: j9k0l1m2n3o4
Revises: i8j9k0l1m2n3
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j9k0l1m2n3o4"
down_revision: str | None = "i8j9k0l1m2n3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("description", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("internal_note", sa.LargeBinary(), nullable=True))
        batch_op.drop_column("model")
        batch_op.drop_column("harness")


def downgrade() -> None:
    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("harness", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("model", sa.String(length=128), nullable=True))
        batch_op.drop_column("internal_note")
        batch_op.drop_column("description")
