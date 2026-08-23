"""Add durable follow-up turns to agent-text threads.

Revision ID: b4c5d6e7f801
Revises: 35394a7d04d9
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.compression import CompressedText
from omnigent.db.db_models import Uuid16

revision: str = "b4c5d6e7f801"
down_revision: str = "35394a7d04d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_text_thread_turns",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("thread_id", Uuid16(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.String(length=64), nullable=False),
        sa.Column("submission_id", Uuid16(), nullable=False),
        sa.Column("question", CompressedText(), nullable=False),
        sa.Column("selected_quote", CompressedText(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("user_item_id", Uuid16(), nullable=True),
        sa.Column("response_id", sa.String(length=64), nullable=True),
        sa.Column("failure_message", CompressedText(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_text_thread_turns_sequence"),
        sa.CheckConstraint(
            "state IN ('queued', 'submitting', 'running', 'answered', 'failed')",
            name="ck_agent_text_thread_turns_state",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "id"),
    )
    op.create_index(
        "ix_agent_text_thread_turns_thread",
        "agent_text_thread_turns",
        ["workspace_id", "conversation_id", "thread_id", "sequence"],
        unique=True,
    )
    op.create_index(
        "ix_agent_text_thread_turns_response",
        "agent_text_thread_turns",
        ["workspace_id", "conversation_id", "response_id"],
    )
    op.create_index(
        "ux_agent_text_thread_turns_request",
        "agent_text_thread_turns",
        ["workspace_id", "conversation_id", "client_request_id"],
        unique=True,
    )
    op.create_index(
        "ux_agent_text_thread_turns_submission",
        "agent_text_thread_turns",
        ["workspace_id", "conversation_id", "submission_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_agent_text_thread_turns_submission", table_name="agent_text_thread_turns")
    op.drop_index("ux_agent_text_thread_turns_request", table_name="agent_text_thread_turns")
    op.drop_index("ix_agent_text_thread_turns_response", table_name="agent_text_thread_turns")
    op.drop_index("ix_agent_text_thread_turns_thread", table_name="agent_text_thread_turns")
    op.drop_table("agent_text_thread_turns")
