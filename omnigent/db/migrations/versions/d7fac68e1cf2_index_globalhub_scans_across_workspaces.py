"""index the GlobalHub queue and events for scans across every workspace

Revision ID: d7fac68e1cf2
Revises: 4e93b9d0e7bd
Create Date: 2026-11-14 00:00:00.000000

The GlobalHub dispatcher and packagers run outside a request, so they read
every workspace and then act inside the workspace of each row. Their scans
filter on the state first: ``ix_agent_queue_items_state_seq`` now starts with
``state`` and ``seq``, and ``ix_task_events_state`` is new. Both tables are
new to GlobalHub and small, so the indexes build in place.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d7fac68e1cf2"
down_revision: str | None = "4e93b9d0e7bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Start the scan indexes with the state."""
    op.drop_index("ix_agent_queue_items_state_seq", table_name="agent_queue_items")
    op.create_index(
        "ix_agent_queue_items_state_seq", "agent_queue_items", ["state", "seq", "workspace_id"]
    )
    op.create_index(
        "ix_task_events_state", "task_events", ["state", "created_at", "workspace_id", "id"]
    )


def downgrade() -> None:
    """Restore the queue index that starts with the workspace, and drop the event index."""
    op.drop_index("ix_task_events_state", table_name="task_events")
    op.drop_index("ix_agent_queue_items_state_seq", table_name="agent_queue_items")
    op.create_index(
        "ix_agent_queue_items_state_seq", "agent_queue_items", ["workspace_id", "state", "seq"]
    )
