"""Add custom workload categories to model settings.

Revision ID: u2c3d4e5f6g7
Revises: t1b2c3d4e5f6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u2c3d4e5f6g7"
down_revision = "t1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_settings",
        sa.Column(
            "workload_custom_categories",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("model_settings", "workload_custom_categories")
