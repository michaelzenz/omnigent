"""Drop the obsolete agent profile discriminator.

Revision ID: q5e6f7a8b9c0
Revises: p4d5e6f7a8b9
Create Date: 2026-08-19 12:05:00.000000

Apply this contract revision only after the stopped-app manual migration has
copied prompt profiles into ``prompt_profiles`` and rewritten session selection
into the dedicated conversation columns. Operators must also remove the old
profile agent rows and their artifacts so they do not reappear as execution
targets once the discriminator is gone.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q5e6f7a8b9c0"
down_revision: str | None = "p4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("auto_select_enabled")


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(sa.Column("auto_select_enabled", sa.Boolean(), nullable=True))
