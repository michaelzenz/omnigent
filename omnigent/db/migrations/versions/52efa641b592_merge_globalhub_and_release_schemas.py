"""Merge the GlobalHub schema branch with the v0.23.0 release schema branch.

Revision ID: 52efa641b592
Revises: b7d41c2e9f06, ffeec66eadb5

Both parent revisions remain unchanged. Upgrade and downgrade only reconcile
Alembic's version rows; the merge itself changes no application schema or data.
"""

from __future__ import annotations

revision: str = "52efa641b592"
down_revision: tuple[str, ...] = ("b7d41c2e9f06", "ffeec66eadb5")
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Join the independently applied parent revisions."""


def downgrade() -> None:
    """Restore both parent version markers without undoing either schema."""
