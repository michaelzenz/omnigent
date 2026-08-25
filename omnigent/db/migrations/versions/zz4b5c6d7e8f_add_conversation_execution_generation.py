"""Add the conversation execution-generation fencing token.

Revision ID: zz4b5c6d7e8f
Revises: zz3a4b5c6d7e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "zz4b5c6d7e8f"
down_revision: str = "zz3a4b5c6d7e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "omnigent_conversation_metadata",
        sa.Column(
            "execution_generation",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("omnigent_conversation_metadata", "execution_generation")
