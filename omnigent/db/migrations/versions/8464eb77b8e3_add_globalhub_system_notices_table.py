"""add the GlobalHub system notices table

Revision ID: 8464eb77b8e3
Revises: a39c579e21ca
Create Date: 2026-11-13 00:00:00.000000

Adds ``system_notices``: the notice pinned on a GlobalHub board, at most one
active per user. Ownership is stamped on the row and every read is
owner-scoped. Additive; there are no foreign-key constraints (Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMBLOB

from omnigent.db.db_models import Uuid16

revision: str = "8464eb77b8e3"
down_revision: str | None = "a39c579e21ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# CompressedText in the model, stored as zstd frames. A MySQL BLOB holds only
# 64 KB, and a notice's body and declared actions can be larger.
_LARGE_COMPRESSED_TEXT = sa.LargeBinary().with_variant(MEDIUMBLOB(), "mysql")


def upgrade() -> None:
    """Create the ``system_notices`` table."""
    op.create_table(
        "system_notices",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body_kind", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("body", _LARGE_COMPRESSED_TEXT, nullable=True),
        sa.Column("actions", _LARGE_COMPRESSED_TEXT, nullable=True),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("publisher_session_id", Uuid16(), nullable=True),
        sa.Column("publisher_label", sa.String(256), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.CheckConstraint("state IN (1, 2)", name="ck_system_notices_state"),
        sa.CheckConstraint("body_kind IN (1, 2)", name="ck_system_notices_body_kind"),
    )
    op.create_index(
        "ix_system_notices_owner_state",
        "system_notices",
        ["workspace_id", "owner_user_id", "state", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the ``system_notices`` table."""
    op.drop_index("ix_system_notices_owner_state", table_name="system_notices")
    op.drop_table("system_notices")
