"""add the GlobalHub custodian sweeps table

Revision ID: 62d77984d129
Revises: d7fac68e1cf2
Create Date: 2026-11-15 00:00:00.000000

Adds ``custodian_sweeps``: when each user's custodian gets its daily session
cleanup message. ``ix_custodian_sweeps_due`` starts with ``next_run_at``,
because the dispatcher finds the due sweeps across every workspace. Ownership
is stamped on the row; there are no foreign-key constraints (Rule R032).

The cleanup message is a new queue item kind (4), so
``ck_agent_queue_items_kind`` now allows it. Downgrade deletes those items
before it restores the old check.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "62d77984d129"
down_revision: str | None = "d7fac68e1cf2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CUSTODIAN_SWEEP_KIND = 4


def upgrade() -> None:
    """Create ``custodian_sweeps`` and allow the cleanup message in the queue."""
    op.create_table(
        "custodian_sweeps",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("local_time", sa.String(5), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        # NULL while the sweep is off.
        sa.Column("next_run_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_custodian_sweeps_owner",
        "custodian_sweeps",
        ["workspace_id", "owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_custodian_sweeps_due",
        "custodian_sweeps",
        ["next_run_at", "workspace_id", "id"],
        unique=False,
    )
    with op.batch_alter_table("agent_queue_items") as batch:
        batch.drop_constraint("ck_agent_queue_items_kind", type_="check")
        batch.create_check_constraint("ck_agent_queue_items_kind", "kind IN (1, 2, 3, 4)")


def downgrade() -> None:
    """Drop ``custodian_sweeps`` and the queued cleanup messages."""
    op.execute(
        sa.text("DELETE FROM agent_queue_items WHERE kind = :kind").bindparams(
            kind=_CUSTODIAN_SWEEP_KIND
        )
    )
    with op.batch_alter_table("agent_queue_items") as batch:
        batch.drop_constraint("ck_agent_queue_items_kind", type_="check")
        batch.create_check_constraint("ck_agent_queue_items_kind", "kind IN (1, 2, 3)")
    op.drop_index("ix_custodian_sweeps_due", table_name="custodian_sweeps")
    op.drop_index("ix_custodian_sweeps_owner", table_name="custodian_sweeps")
    op.drop_table("custodian_sweeps")
