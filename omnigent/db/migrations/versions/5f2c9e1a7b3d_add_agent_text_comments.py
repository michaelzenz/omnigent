"""add users' unsent comments on agent text

Revision ID: 5f2c9e1a7b3d
Revises: 1306afe778d8
Create Date: 2026-11-15 00:00:00.000000

Adds ``agent_text_comments``: each row is one user's comment on a span of a
completed assistant message. A user's comments in a session form their unsent
batch, which ``ix_agent_text_comments_owner`` finds. Ownership is stamped on
the row; there are no foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.compression import CompressedText
from omnigent.db.db_models import Uuid16

revision: str = "5f2c9e1a7b3d"
down_revision: str | None = "1306afe778d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``agent_text_comments`` and its index."""
    op.create_table(
        "agent_text_comments",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("conversation_item_id", Uuid16(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("selected_text", CompressedText(), nullable=False),
        sa.Column("prefix_context", CompressedText(), nullable=False),
        sa.Column("suffix_context", CompressedText(), nullable=False),
        sa.Column("body", CompressedText(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("start_offset >= 0", name="ck_agent_text_comments_start"),
        sa.CheckConstraint("end_offset > start_offset", name="ck_agent_text_comments_range"),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "id"),
    )
    op.create_index(
        "ix_agent_text_comments_owner",
        "agent_text_comments",
        ["workspace_id", "conversation_id", "owner_user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop ``agent_text_comments`` and its index."""
    op.drop_index("ix_agent_text_comments_owner", table_name="agent_text_comments")
    op.drop_table("agent_text_comments")
