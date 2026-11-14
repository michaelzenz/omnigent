"""count the notices that carried a GlobalHub task event

Revision ID: 4e93b9d0e7bd
Revises: cb3d85b64919
Create Date: 2026-11-13 00:00:00.000000

Adds ``task_events.delivery_count``: how many sent queue items carried the
event since it last passed to another role. The broker gets an event it did
not route again only while the count is under its cap. Additive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e93b9d0e7bd"
down_revision: str | None = "cb3d85b64919"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``task_events.delivery_count``."""
    op.add_column(
        "task_events",
        sa.Column("delivery_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Drop ``task_events.delivery_count``."""
    # DROP COLUMN needs batch_alter_table on older SQLite.
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_column("delivery_count")
