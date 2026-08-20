"""Add the editable OmniHarness base system prompt."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v3d4e5f6g7h8"
down_revision = "u2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_settings",
        sa.Column(
            "omniharness_system_prompt",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("model_settings", "omniharness_system_prompt")
