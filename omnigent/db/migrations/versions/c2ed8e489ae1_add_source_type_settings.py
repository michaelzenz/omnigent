"""Add the routing of each GlobalHub source type.

Revision ID: c2ed8e489ae1
Revises: e4b19c7d2a63
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "c2ed8e489ae1"
down_revision: str | None = "e4b19c7d2a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_type_settings",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("routing", sa.SmallInteger(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("routing IN (1, 2)", name="ck_source_type_settings_routing"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_source_type_settings_type",
        "source_type_settings",
        ["workspace_id", "owner_user_id", "source_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_type_settings_type", table_name="source_type_settings")
    op.drop_table("source_type_settings")
