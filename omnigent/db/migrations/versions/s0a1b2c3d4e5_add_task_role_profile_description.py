"""Add task_role_profiles.description for role selection

Revision ID: s0a1b2c3d4e5
Revises: r0a1b2c3d4e5
Create Date: 2026-08-15 17:15:00.000000

Adds a nullable ``description`` TEXT column to ``task_role_profiles``. The
manager lists worker roles to pick one for a new lane; the description tells
it what each role specializes in (the title is only a display label). Packaged
roles seed a default description on first upsert; custom roles carry a
user-authored description or stay NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s0a1b2c3d4e5"
down_revision: str | None = "r0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_role_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("task_role_profiles", schema=None) as batch_op:
        batch_op.drop_column("description")
