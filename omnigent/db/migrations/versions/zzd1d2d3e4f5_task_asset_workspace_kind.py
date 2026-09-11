"""Task assets: allow kind='workspace'.

Revision ID: zzd1d2d3e4f5
Revises: zze4f5a6b7c8
Create Date: 2026-09-07 00:00:00.000000

Extends the ``task_assets.kind`` CHECK to accept ``workspace`` assets —
references to a working directory the card opens in the configured editor.
Also reclassifies any category-only 'workspace' rows (none expected) and
leaves existing 'url' rows untouched.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "zzd1d2d3e4f5"
down_revision: str | None = "zze4f5a6b7c8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_assets", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_assets_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_task_assets_kind",
            "kind IN ('url', 'workspace')",
        )
        batch_op.drop_constraint("ck_task_assets_category", type_="check")
        batch_op.create_check_constraint(
            "ck_task_assets_category",
            "category IN ('code', 'tests', 'documents', 'logs', 'other', 'workspace')",
        )
    # URL is the asset identity (one asset per URL): collapse any accidental
    # duplicates (keeping the lowest id) so the unique index below holds, then
    # enforce it so upsert_asset's ON CONFLICT is race-safe. NULL urls are
    # distinct rows (NULLs never collide in unique indexes) and are left alone.
    op.execute(
        "DELETE FROM task_assets WHERE url IS NOT NULL AND id NOT IN ("
        "SELECT MIN(id) FROM task_assets WHERE url IS NOT NULL "
        "GROUP BY workspace_id, task_id, url)"
    )
    op.create_index(
        "uq_task_assets_url",
        "task_assets",
        ["workspace_id", "task_id", "url"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_task_assets_url", table_name="task_assets")
    op.execute("DELETE FROM task_assets WHERE kind = 'workspace' OR category = 'workspace'")
    with op.batch_alter_table("task_assets", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_assets_category", type_="check")
        batch_op.create_check_constraint(
            "ck_task_assets_category",
            "category IN ('code', 'tests', 'documents', 'logs', 'other')",
        )
        batch_op.drop_constraint("ck_task_assets_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_task_assets_kind",
            "kind IN ('url')",
        )
