"""Add retry_count to agent_queue_items for dispatch retry backoff.

Revision ID: zz5b6c7d8e9f0
Revises: zz4b5c6d7e8f
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "zz5b6c7d8e9f0"
down_revision: str = "zz4b5c6d7e8f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_queue_items",
        sa.Column(
            "retry_count",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_queue_items", "retry_count")
