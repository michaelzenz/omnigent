"""add ambient codex sync columns to conversation metadata

Revision ID: b1c2d3e4f5a6
Revises: a7b3c4d5e6f7
Create Date: 2026-07-18 00:00:00.000000

Persists Codex ambient-bridge poll cursors on the server so host
daemons can hydrate track state after restart.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "o1b2c3d4e5f6"
down_revision: str | None = "a7b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ambient Codex columns to ``omnigent_conversation_metadata``."""
    with op.batch_alter_table("omnigent_conversation_metadata") as batch_op:
        batch_op.add_column(sa.Column("ambient_poller_host_id", Uuid16(), nullable=True))
        batch_op.add_column(sa.Column("ambient_byte_offset", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("ambient_rollout_path", sa.String(2048), nullable=True))
        batch_op.add_column(sa.Column("ambient_turn_id", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("ambient_connection_id", sa.String(64), nullable=True))
    op.create_index(
        "ix_conversation_metadata_ambient_poller_host_id",
        "omnigent_conversation_metadata",
        ["workspace_id", "ambient_poller_host_id"],
    )


def downgrade() -> None:
    """Drop ambient Codex columns from ``omnigent_conversation_metadata``."""
    op.drop_index(
        "ix_conversation_metadata_ambient_poller_host_id",
        table_name="omnigent_conversation_metadata",
    )
    with op.batch_alter_table("omnigent_conversation_metadata") as batch_op:
        batch_op.drop_column("ambient_connection_id")
        batch_op.drop_column("ambient_turn_id")
        batch_op.drop_column("ambient_rollout_path")
        batch_op.drop_column("ambient_byte_offset")
        batch_op.drop_column("ambient_poller_host_id")
