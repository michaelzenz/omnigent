"""Merge the session-watcher and upstream 0.9 branches.

Revision ID: m0a1b2c3d4e5
Revises: z0a1b2c3d4e5, za2b3c4d5e6f
Create Date: 2026-08-14 00:00:00.000000

Merges the two migration heads after the upstream 0.9 merge:
- ``z0a1b2c3d4e5`` — the session-watcher branch (relax workers constraint)
- ``za2b3c4d5e6f`` — the upstream 0.9 branch (task summary on conversation metadata)
"""

from __future__ import annotations

from alembic import op

revision: str = "m0a1b2c3d4e5"
down_revision: str | None = ("z0a1b2c3d4e5", "za2b3c4d5e6f")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
