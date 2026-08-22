"""Add external_session_hint to workers for session watcher.

Revision ID: z9a3b4c5d6e7
Revises: z8a2b3c4d5e6
Create Date: 2026-08-13 00:00:00.000000

Adds an ``external_session_hint`` column to the ``workers`` table so the
ingress layer can auto-route ``external.session.updated`` events to the task
bound to an adopted external session.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "z9a3b4c5d6e7"
down_revision: str | None = "d9a3b02f8e51"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "workers",
        sa.Column("external_session_hint", sa.String(256), nullable=True),
    )
    op.create_index(
        "ix_workers_external_hint",
        "workers",
        ["workspace_id", "external_session_hint"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_workers_external_hint", table_name="workers")
    op.drop_column("workers", "external_session_hint")
