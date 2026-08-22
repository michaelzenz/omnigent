"""Add agents.is_role flag for role-bound agent profiles

Revision ID: r0a1b2c3d4e5
Revises: m0a1b2c3d4e5
Create Date: 2026-08-15 16:45:00.000000

Adds a boolean ``is_role`` column to ``agents``. When true the agent is a
role-bound profile (the prompt backing a glossary role) and is hidden from
the public ``GET /v1/agents`` catalog so it doesn't clutter the New Chat
picker. Lookups by id/name are unaffected, so role session bootstrap still
resolves it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r0a1b2c3d4e5"
down_revision: str | None = "m0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_role", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("is_role")
