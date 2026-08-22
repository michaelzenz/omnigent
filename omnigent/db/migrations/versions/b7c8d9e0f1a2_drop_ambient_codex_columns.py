"""drop ambient codex sync columns from conversation metadata

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-08-06 21:30:00.000000

Ambient session mirroring is gone: an external TUI holds its conversation
in memory and never re-reads its transcript, so a mirrored copy could be
read but never steered. The poll cursors these columns held have no
remaining reader.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "a6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    """Restore ambient Codex columns on ``omnigent_conversation_metadata``."""
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
