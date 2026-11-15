"""add the GlobalHub role schedules table

Revision ID: 62d77984d129
Revises: d7fac68e1cf2
Create Date: 2026-11-15 00:00:00.000000

Adds ``role_schedules``: when each user's GlobalHub role sessions get their
scheduled run message, e.g. the custodian's session cleanup. Each row holds an
RFC 5545 rule and its timezone. ``ix_role_schedules_owner`` is unique, so an
owner has at most one schedule per role. ``ix_role_schedules_due`` starts with
``next_run_at``, because the dispatcher finds the due schedules across every
workspace. Ownership is stamped on the row; there are no foreign-key
constraints (Rule R032).

The run message is a new queue item kind (4), so ``ck_agent_queue_items_kind``
now allows it. Downgrade deletes those items before it restores the old check.
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

_SCHEDULED_RUN_KIND = 4


def upgrade() -> None:
    """Create ``role_schedules`` and allow the run message in the queue."""
    op.create_table(
        "role_schedules",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        # NULL in single-user mode.
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("role_key", sa.String(128), nullable=False),
        sa.Column("rrule", sa.String(512), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        # NULL while the schedule is off.
        sa.Column("next_run_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_role_schedules_owner",
        "role_schedules",
        ["workspace_id", "owner_user_id", "role_key"],
        unique=True,
    )
    op.create_index(
        "ix_role_schedules_due",
        "role_schedules",
        ["next_run_at", "workspace_id", "id"],
        unique=False,
    )
    with op.batch_alter_table("agent_queue_items") as batch:
        batch.drop_constraint("ck_agent_queue_items_kind", type_="check")
        batch.create_check_constraint("ck_agent_queue_items_kind", "kind IN (1, 2, 3, 4)")


def downgrade() -> None:
    """Drop ``role_schedules`` and the queued run messages."""
    op.execute(
        sa.text("DELETE FROM agent_queue_items WHERE kind = :kind").bindparams(
            kind=_SCHEDULED_RUN_KIND
        )
    )
    with op.batch_alter_table("agent_queue_items") as batch:
        batch.drop_constraint("ck_agent_queue_items_kind", type_="check")
        batch.create_check_constraint("ck_agent_queue_items_kind", "kind IN (1, 2, 3)")
    op.drop_index("ix_role_schedules_due", table_name="role_schedules")
    op.drop_index("ix_role_schedules_owner", table_name="role_schedules")
    op.drop_table("role_schedules")
