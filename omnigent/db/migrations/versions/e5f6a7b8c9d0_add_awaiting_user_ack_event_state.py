"""legacy no-op — task event states are defined in c2d3e4f5a6b7

Revision ID: e5f6a7b8c9d0
Revises: e4f5a6b7c8d0
Create Date: 2026-07-21 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "e4f5a6b7c8d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op — superseded by the base agent-task migration."""


def downgrade() -> None:
    """No-op — superseded by the base agent-task migration."""
