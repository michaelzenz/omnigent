"""Add open_folders to conversation metadata.

Revision ID: 7c4e2b9a1d6f
Revises: 5f2c9e1a7b3d

Adds a nullable, compressed ``open_folders`` column to
``omnigent_conversation_metadata``: the folders that a session's agent stamped
for the user to open in Cursor or VS Code.

Additive with no default or backfill. Deploy before the server that writes it;
older servers ignore the column. Downgrade discards the stamps, which agents
can stamp again.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c4e2b9a1d6f"
down_revision: str | None = "5f2c9e1a7b3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "omnigent_conversation_metadata"


def upgrade() -> None:
    """Add ``open_folders`` to ``omnigent_conversation_metadata``."""
    op.add_column(_TABLE, sa.Column("open_folders", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    """Remove ``open_folders`` from ``omnigent_conversation_metadata``."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column("open_folders")
