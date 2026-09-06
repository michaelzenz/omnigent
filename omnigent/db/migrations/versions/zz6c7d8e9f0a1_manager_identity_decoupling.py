"""Manager identity decoupling: durable manager_id replaces session-id keying.

The manager session id was the identity everywhere (managers PK, tasks,
task_events, queue scope). A deleted session stranded all of it. The
``managers`` row is now the durable identity (uuid PK) with a swappable
``conversation_id`` session pointer, mirroring workers (worker.id scope +
worker.target_id).

The row is self-describing: title plus the execution snapshot (host,
workspace, harness, model, agent profile, prompt profile) taken at spawn
are stored on the row, so session re-creation reads only the manager —
never the task or role profile that happened to trigger it.

Feature is in development with no users, so all state tied to the old
keying is erased: every manager row, manager-role queues and items, and
non-terminal task events. Tasks re-bootstrap on their next routed event.

Revision ID: zz6c7d8e9f0a1
Revises: gb1b2c3d4e5f, c7d8e9f0a1b3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "zz6c7d8e9f0a1"
down_revision: tuple[str, str] = ("gb1b2c3d4e5f", "c7d8e9f0a1b3")
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # ── Erase data keyed by the old session-id identity ─────────────────────
    op.execute("DELETE FROM agent_queue_items WHERE role = 'manager'")
    op.execute("DELETE FROM agent_queues WHERE role = 'manager'")
    op.execute(
        "DELETE FROM task_events WHERE state IN (1, 4, 6, 9, 13, 14)"
    )  # received, awaiting_grouping, routed, failed, broadcast, pending_triage

    # ── managers: rebuild with uuid PK + full self-describing schema ────────
    # Every row is erased: old rows carried no execution snapshot, so none
    # could be healed under the new model. Tasks re-attach via the normal
    # bootstrap on their next routed event.
    op.drop_index("ix_managers_owner", table_name="managers")
    op.drop_table("managers")
    op.create_table(
        "managers",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=True),
        sa.Column("owner_user_id", sa.String(128), nullable=False),
        sa.Column("role_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("host_id", sa.String(64), nullable=True),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column("harness", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("agent_profile_id", Uuid16(), nullable=True),
        sa.Column("prompt_profile_id", Uuid16(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_managers_owner",
        "managers",
        ["workspace_id", "owner_user_id", "created_at", "id"],
    )

    # ── tasks: manager_conversation_id → manager_id ─────────────────────────
    op.drop_index("ix_tasks_manager_conversation", table_name="tasks")
    op.drop_column("tasks", "manager_conversation_id")
    op.add_column("tasks", sa.Column("manager_id", Uuid16(), nullable=True))
    op.create_index("ix_tasks_manager", "tasks", ["workspace_id", "manager_id"])

    # ── task_events: manager_conversation_id → manager_id ───────────────────
    # Terminal rows keep their history with manager_id nulled; the old
    # session ids are meaningless under the new identity model.
    op.drop_index("ix_task_events_manager_state", table_name="task_events")
    op.drop_column("task_events", "manager_conversation_id")
    op.add_column("task_events", sa.Column("manager_id", Uuid16(), nullable=True))
    op.create_index(
        "ix_task_events_manager_state",
        "task_events",
        ["workspace_id", "manager_id", "state", "created_at", "id"],
    )


def downgrade() -> None:
    # Irreversible by design: erased pipeline data cannot be reconstructed.
    raise NotImplementedError("manager identity decoupling is not reversible")
