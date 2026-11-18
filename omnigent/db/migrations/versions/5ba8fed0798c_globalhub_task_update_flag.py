"""Add the update flag of GlobalHub tasks.

Revision ID: 5ba8fed0798c
Revises: 8e2b6d4f1a93

Adds ``tasks.has_update``. A manager flags news on a task for the user, and the
task's card shows a "Has update" badge until the user clicks the card.
``server_default=sa.false()`` gives the existing tasks no flag. Downgrade drops
the column, with the flags in it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5ba8fed0798c"
down_revision: str | None = "8e2b6d4f1a93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tasks"


def upgrade() -> None:
    """Add ``has_update`` to ``tasks``, false for every task."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(
            sa.Column("has_update", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    """Drop ``has_update`` from ``tasks``."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column("has_update")
