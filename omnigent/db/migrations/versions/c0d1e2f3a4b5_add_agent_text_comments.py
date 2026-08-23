"""Add unsent comments anchored to finalized agent text.

Revision ID: c0d1e2f3a4b5
Revises: b9d0e1f2a3b4
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.compression import CompressedText
from omnigent.db.db_models import Uuid16

revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_text_comments",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("conversation_item_id", Uuid16(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("selected_text", CompressedText(), nullable=False),
        sa.Column("prefix_context", CompressedText(), nullable=False),
        sa.Column("suffix_context", CompressedText(), nullable=False),
        sa.Column("body", CompressedText(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("start_offset >= 0", name="ck_agent_text_comments_start"),
        sa.CheckConstraint("end_offset > start_offset", name="ck_agent_text_comments_range"),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "id"),
    )
    op.create_index(
        "ix_agent_text_comments_item",
        "agent_text_comments",
        ["workspace_id", "conversation_id", "conversation_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_text_comments_item", table_name="agent_text_comments")
    op.drop_table("agent_text_comments")
