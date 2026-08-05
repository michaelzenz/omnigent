"""add interrupted item states, drop approved, drop queue item priority

Revision ID: a6b7c8d9e0f1
Revises: z5a6b7c8d9e0
Create Date: 2026-08-05 14:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "z5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the ``interrupted`` state to both item tables and drop ``priority``.

    ``interrupted`` is a *parked* state on ``task_items`` (9) and
    ``agent_queue_items`` (6): work the user stopped, which halts the queue and
    waits for a retry or a removal. It is deliberately distinct from ``done``
    (the turn ended on its own, however it ended) and from ``cancelled`` (the
    item is gone for good).

    ``task_items.approved`` (3) is removed in the same pass. Nothing ever wrote
    it — accepting an item moves it straight from ``awaiting_user_ack`` to
    ``queued`` — so it only ever misled readers of the enum. Any stray row is
    swept to ``queued`` first so the narrowed check cannot fail.

    ``agent_queue_items.priority`` goes because dispatch is strict insert order.
    It must leave the drain index too: an unused column sitting between
    ``state`` and ``seq`` would stop that index serving the ordered scan the
    dispatcher depends on.
    """
    op.get_bind().execute(sa.text("UPDATE task_items SET state = 4 WHERE state = 3"))
    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_items_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_items_state",
            "state IN (1, 2, 4, 5, 6, 7, 8, 9)",
        )

    with op.batch_alter_table("agent_queue_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_agent_queue_items_state", type_="check")
        batch_op.create_check_constraint(
            "ck_agent_queue_items_state",
            "state IN (1, 2, 3, 4, 5, 6)",
        )

    op.drop_index("ix_agent_queue_items_drain", table_name="agent_queue_items")
    with op.batch_alter_table("agent_queue_items", schema=None) as batch_op:
        batch_op.drop_column("priority")
    op.create_index(
        "ix_agent_queue_items_drain",
        "agent_queue_items",
        [
            "workspace_id",
            "role",
            "owner_user_id",
            "scope_id",
            "state",
            "seq",
        ],
        unique=False,
    )


def downgrade() -> None:
    """Restore ``priority`` and narrow both state checks.

    Parked items become ``queued`` again — the state they would hold had the
    interrupt never happened — because the older schema has no way to express
    "stopped, waiting on the user". Rows are rewritten before each check is
    narrowed so the constraint cannot reject them.
    """
    op.drop_index("ix_agent_queue_items_drain", table_name="agent_queue_items")
    with op.batch_alter_table("agent_queue_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
        )
    op.create_index(
        "ix_agent_queue_items_drain",
        "agent_queue_items",
        [
            "workspace_id",
            "role",
            "owner_user_id",
            "scope_id",
            "state",
            "priority",
            "seq",
        ],
        unique=False,
    )

    op.get_bind().execute(sa.text("UPDATE agent_queue_items SET state = 1 WHERE state = 6"))
    with op.batch_alter_table("agent_queue_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_agent_queue_items_state", type_="check")
        batch_op.create_check_constraint(
            "ck_agent_queue_items_state",
            "state IN (1, 2, 3, 4, 5)",
        )

    op.get_bind().execute(sa.text("UPDATE task_items SET state = 4 WHERE state = 9"))
    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_task_items_state", type_="check")
        batch_op.create_check_constraint(
            "ck_task_items_state",
            "state IN (1, 2, 3, 4, 5, 6, 7, 8)",
        )
