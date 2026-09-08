"""Workers: add 'internal' kind; reclassify adopted local sessions.

Revision ID: zzc1d2d3e4f5
Revises: zzb1c2d3e4f5
Create Date: 2026-09-07 00:00:00.000000

Extends the worker kind taxonomy:
- ``managed``  — dispatched by the task system (unchanged)
- ``internal`` — adopted omnigent sessions (live in this server's
  conversation store; chat-able, server-visible status)
- ``external`` — non-omnigent sessions from external harnesses
  (Claude Code, Codex, ...) discovered by the watcher

Existing ``external`` rows whose ``target_id`` is a local conversation are
reclassified as ``internal``. Skipped when the conversations table is not in
this database (split-DB deployments keep their rows as-is).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "zzc1d2d3e4f5"
down_revision: str | None = "zzb1c2d3e4f5"
branch_labels: str | None = None
depends_on: str | None = None


def _conversation_id_expr(dialect: str) -> str:
    """SQL expression rendering conversations.id as bare 32-char lowercase hex.

    conversations.id is a Uuid16 (raw 16 bytes on every backend); workers
    .target_id is text hex, so the membership check compares hex to hex.
    """
    if dialect == "postgresql":
        return "encode(id, 'hex')"
    # SQLite and MySQL both expose hex(); SQLite returns uppercase.
    return "lower(hex(id))"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.drop_constraint("ck_workers_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_workers_kind",
            "kind IN ('managed', 'internal', 'external')",
        )
    if not inspector.has_table("conversations"):
        return
    dialect = bind.dialect.name
    targets = (
        bind.execute(
            sa.text(
                "SELECT DISTINCT target_id FROM workers "
                "WHERE kind = 'external' AND target_id IS NOT NULL"
            )
        )
        .scalars()
        .all()
    )
    conversation_ids = {
        row[0]
        for row in bind.execute(sa.text(f"SELECT {_conversation_id_expr(dialect)} FROM conversations"))
    }
    for target_id in targets:
        if target_id in conversation_ids:
            bind.execute(
                sa.text(
                    "UPDATE workers SET kind = 'internal' "
                    "WHERE kind = 'external' AND target_id = :target_id"
                ).bindparams(target_id=target_id)
            )


def downgrade() -> None:
    op.execute("UPDATE workers SET kind = 'external' WHERE kind = 'internal'")
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.drop_constraint("ck_workers_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_workers_kind",
            "kind IN ('managed', 'external')",
        )
