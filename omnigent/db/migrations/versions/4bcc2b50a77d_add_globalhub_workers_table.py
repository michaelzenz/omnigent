"""add the GlobalHub workers table

Revision ID: 4bcc2b50a77d
Revises: 1a547c56498a
Create Date: 2026-11-12 00:00:00.000000

Adds ``workers``: each wraps one session for one GlobalHub task. Ownership is
stamped on the row and every read is owner-scoped. Additive; there are no
foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "4bcc2b50a77d"
down_revision: str | None = "1a547c56498a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``workers`` table."""
    op.create_table(
        "workers",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("task_id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("session_id", Uuid16(), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_workers_task_session",
        "workers",
        ["workspace_id", "task_id", "session_id"],
        unique=True,
    )
    op.create_index("ix_workers_session", "workers", ["workspace_id", "session_id"], unique=False)


def downgrade() -> None:
    """Drop the ``workers`` table."""
    op.drop_index("ix_workers_session", table_name="workers")
    op.drop_index("ix_workers_task_session", table_name="workers")
    op.drop_table("workers")
