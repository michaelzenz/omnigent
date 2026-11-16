"""Drop goal from GlobalHub tasks.

Revision ID: 9b3f6d2a8c41
Revises: 7c4e2b9a1d6f

Drops the ``goal`` column from ``tasks``: a task's Overview (its
``description``) now starts with the goal, so the board no longer keeps a
separate one.

Destructive: the stored goals go away. Deploy with the server that stops
reading the column. Downgrade adds the column back with an empty goal for
every task.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b3f6d2a8c41"
down_revision: str | None = "7c4e2b9a1d6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tasks"


def upgrade() -> None:
    """Drop ``goal`` from ``tasks``."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column("goal")


def downgrade() -> None:
    """Add ``goal`` back to ``tasks``, empty for every task."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column("goal", sa.Text(), nullable=False, server_default=""))
