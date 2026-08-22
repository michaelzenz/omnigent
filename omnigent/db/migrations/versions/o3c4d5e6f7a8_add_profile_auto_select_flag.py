"""Add explicit profile Auto Select membership.

Revision ID: o3c4d5e6f7a8
Revises: n2b3c4d5e6f7
Create Date: 2026-08-19 09:49:00.000000
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "o3c4d5e6f7a8"
down_revision: str | None = "n2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.hex()
    return str(value).replace("-", "")


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(sa.Column("auto_select_enabled", sa.Boolean(), nullable=True))

    bind = op.get_bind()
    agents = sa.Table("agents", sa.MetaData(), autoload_with=bind)
    rows = bind.execute(
        sa.select(
            agents.c.workspace_id,
            agents.c.id,
            agents.c.name,
            agents.c.kind,
            agents.c.is_role,
            agents.c.enabled,
            agents.c.archived,
        )
    ).mappings()
    for row in rows:
        builtin_id = hashlib.sha256(f"builtin:{row['name']}".encode()).hexdigest()[:32]
        is_profile = row["kind"] == 1 and not row["is_role"] and _id_text(row["id"]) != builtin_id
        if not is_profile:
            continue
        bind.execute(
            agents.update()
            .where(
                agents.c.workspace_id == row["workspace_id"],
                agents.c.id == row["id"],
            )
            .values(
                auto_select_enabled=bool(row["enabled"] and not row["archived"]),
                enabled=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("auto_select_enabled")
