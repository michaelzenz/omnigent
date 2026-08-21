"""Add the turn-selection user-message count setting."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "x5f6g7h8i9j0"
down_revision = "w4e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_settings",
        sa.Column(
            "turn_selection_user_message_count",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
    )


def downgrade() -> None:
    op.drop_column("model_settings", "turn_selection_user_message_count")
