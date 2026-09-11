"""Task assets: add source_worker_id provenance.

Revision ID: zzb7c8d9e0f1
Revises: zzf6a7b8c9d0
Create Date: 2026-09-08 00:00:00.000000

Adds ``task_assets.source_worker_id`` — the worker lane an asset was
harvested from (adoption auto-attach stamps the adopted worker; manual
adds leave it NULL). The card renders an expandable "from <worker>"
chip with a jump-to-chat button; unknown ids render as "worker removed".
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "zzb7c8d9e0f1"
down_revision: str | None = "zzf6a7b8c9d0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_worker_id", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("task_assets", schema=None) as batch_op:
        batch_op.drop_column("source_worker_id")
