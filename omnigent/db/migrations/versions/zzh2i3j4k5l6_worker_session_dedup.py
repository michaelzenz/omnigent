"""Collapse duplicate (task, session) worker rows.

The adoption endpoint deduped against only the session's oldest worker row;
once a session was bound to a second task, every later re-adoption of that
second task created a fresh duplicate lane. This collapses existing
duplicates: per (task_id, target_id) the newest row is kept, the rest
are soft-deleted, and references (task items, open queue entries, asset
provenance) are re-pointed to the kept row. All rows in a group share the
same underlying session, so re-pointing changes nothing about the work.

Revision ID: zzh2i3j4k5l6
Revises: zzg1h2i3j4k5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from omnigent.db.utils import now_epoch

revision: str = "zzh2i3j4k5l6"
down_revision: str | None = "zzg1h2i3j4k5"
branch_labels = None
depends_on = None

# agent_queue_items states that can still be dispatched/retried:
# queued, dispatched, dispatch_failed, interrupted.
_OPEN_QUEUE_STATES = (1, 2, 5, 6)


def _newest(members: list[dict]) -> dict:
    return max(members, key=lambda m: (m["created_at"] or 0, m["id"]))


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT workspace_id, task_id, target_id, id, state, created_at "
            "FROM workers WHERE target_id IS NOT NULL"
        )
    ).fetchall()

    groups: dict[tuple, list[dict]] = {}
    for ws_id, task_id, target_id, worker_id, state, created_at in rows:
        key = (ws_id, task_id, target_id)
        groups.setdefault(key, []).append(
            {
                "workspace_id": ws_id,
                "task_id": task_id,
                "target_id": target_id,
                "id": worker_id,
                "state": state,
                "created_at": created_at,
            }
        )

    now = now_epoch()
    collapsed: list[tuple[dict, dict]] = []  # (dropped, kept)
    for members in groups.values():
        if len(members) < 2:
            continue
        kept = _newest(members)
        for member in members:
            if member["id"] != kept["id"]:
                collapsed.append((member, kept))
    if not collapsed:
        return

    conn.execute(
        sa.text(
            "UPDATE workers SET state = 'deleted', updated_at = :now "
            "WHERE workspace_id = :workspace_id AND id = :worker_id"
        ),
        [
            {"now": now, "workspace_id": dropped["workspace_id"], "worker_id": dropped["id"]}
            for dropped, _kept in collapsed
        ],
    )
    conn.execute(
        sa.text(
            "UPDATE task_items SET worker_id = :kept_id, updated_at = :now "
            "WHERE workspace_id = :workspace_id AND worker_id = :old_id"
        ),
        [
            {
                "kept_id": kept["id"],
                "now": now,
                "workspace_id": dropped["workspace_id"],
                "old_id": dropped["id"],
            }
            for dropped, kept in collapsed
        ],
    )
    conn.execute(
        sa.text(
            "UPDATE agent_queue_items SET scope_id = :kept_id "
            "WHERE workspace_id = :workspace_id AND role = 'worker' "
            "AND scope_id = :old_id AND state IN (1, 2, 5, 6)"
        ),
        [
            {
                "kept_id": kept["id"],
                "workspace_id": dropped["workspace_id"],
                "old_id": dropped["id"],
            }
            for dropped, kept in collapsed
        ],
    )
    conn.execute(
        sa.text(
            "UPDATE task_assets SET source_worker_id = :kept_id "
            "WHERE workspace_id = :workspace_id AND source_worker_id = :old_id"
        ),
        [
            {
                "kept_id": kept["id"],
                "workspace_id": dropped["workspace_id"],
                "old_id": dropped["id"],
            }
            for dropped, kept in collapsed
        ],
    )


def downgrade() -> None:
    # Collapsed duplicates cannot be reconstructed.
    pass
