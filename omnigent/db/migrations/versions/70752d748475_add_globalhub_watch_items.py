"""add the GlobalHub watch items table

Revision ID: 70752d748475
Revises: 57e5a00c4b94
Create Date: 2026-11-15 00:00:00.000000

Adds ``watch_items``: what each watcher tracks, with the named offsets it saw
last. ``ix_watch_items_key`` is unique, so a watcher tracks a thing once per
source and source key; its key columns stay short enough for MySQL's index
limit. The two other indexes serve a run's least recently checked items and
the stable pages of a watcher's list. Ownership is stamped on the row; there
are no foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "70752d748475"
down_revision: str | None = "57e5a00c4b94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``watch_items`` and its indexes."""
    op.create_table(
        "watch_items",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("watcher_id", Uuid16(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(512), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("offsets", sa.Text(), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.Integer(), nullable=True),
        sa.Column("done_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.CheckConstraint("status IN (1, 2)", name="ck_watch_items_status"),
    )
    op.create_index(
        "ix_watch_items_key",
        "watch_items",
        ["workspace_id", "watcher_id", "source", "source_key"],
        unique=True,
    )
    op.create_index(
        "ix_watch_items_watcher_checked",
        "watch_items",
        ["workspace_id", "watcher_id", "status", "checked_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_watch_items_watcher_created",
        "watch_items",
        ["workspace_id", "watcher_id", "status", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop ``watch_items`` and its indexes."""
    op.drop_index("ix_watch_items_watcher_created", table_name="watch_items")
    op.drop_index("ix_watch_items_watcher_checked", table_name="watch_items")
    op.drop_index("ix_watch_items_key", table_name="watch_items")
    op.drop_table("watch_items")
