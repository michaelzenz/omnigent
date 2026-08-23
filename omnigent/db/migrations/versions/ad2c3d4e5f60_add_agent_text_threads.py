"""Add individually-sent threads anchored to finalized agent text.

Revision ID: ad2c3d4e5f60
Revises: c0d1e2f3a4b5, ac1b2c3d4e5f
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.compression import CompressedText
from omnigent.db.db_models import Uuid16

revision: str = "ad2c3d4e5f60"
down_revision: tuple[str, str] = ("c0d1e2f3a4b5", "ac1b2c3d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_text_threads",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("source_item_id", Uuid16(), nullable=False),
        sa.Column("client_request_id", sa.String(length=64), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("selected_text", CompressedText(), nullable=False),
        sa.Column("prefix_context", CompressedText(), nullable=False),
        sa.Column("suffix_context", CompressedText(), nullable=False),
        sa.Column("user_comment", CompressedText(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("user_item_id", Uuid16(), nullable=True),
        sa.Column("response_id", sa.String(length=64), nullable=True),
        sa.Column("failure_message", CompressedText(), nullable=True),
        sa.Column("resolved_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("start_offset >= 0", name="ck_agent_text_threads_start"),
        sa.CheckConstraint("end_offset > start_offset", name="ck_agent_text_threads_range"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'answered', 'failed', 'resolved')",
            name="ck_agent_text_threads_state",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "id"),
    )
    op.create_index(
        "ix_agent_text_threads_item",
        "agent_text_threads",
        ["workspace_id", "conversation_id", "source_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_text_threads_response",
        "agent_text_threads",
        ["workspace_id", "conversation_id", "response_id"],
        unique=False,
    )
    op.create_index(
        "ux_agent_text_threads_request",
        "agent_text_threads",
        ["workspace_id", "conversation_id", "client_request_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_agent_text_threads_request", table_name="agent_text_threads")
    op.drop_index("ix_agent_text_threads_response", table_name="agent_text_threads")
    op.drop_index("ix_agent_text_threads_item", table_name="agent_text_threads")
    op.drop_table("agent_text_threads")
