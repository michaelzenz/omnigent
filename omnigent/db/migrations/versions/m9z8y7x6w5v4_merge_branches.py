"""Merge the timer-drop and external-session-hint branches.

Revision ID: m9z8y7x6w5v4
Revises: d9a3b02f8e51, z9a3b4c5d6e7
Create Date: 2026-08-13 00:00:00.000000

Merges the two migration heads that branched from the common root:
- ``z8a2b3c4d5e6`` — the z-chain (UUID conversion + conversation_items PK)
- ``z9a3b4c5d6e7`` — the timer-items drop + role-definition split + external_session_hint
"""

from __future__ import annotations

revision: str = "m9z8y7x6w5v4"
down_revision: str | None = ("z8a2b3c4d5e6", "z9a3b4c5d6e7")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
