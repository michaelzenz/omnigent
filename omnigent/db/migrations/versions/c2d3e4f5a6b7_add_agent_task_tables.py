"""add agent task routing tables

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-20 00:00:00.000000

Adds tables for managed tasks, inbound task events, routing attempts/resolutions,
and worker execution history. No database-level foreign keys (schema Rule R032).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create task routing and execution tables."""
    op.create_table(
        "tasks",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("manager_agent_id", Uuid16(), nullable=False),
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.LargeBinary(), nullable=True),
        sa.Column("charter", sa.Text(), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("state IN (1, 2, 3, 4)", name="ck_tasks_state"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_tasks_state_updated",
        "tasks",
        ["workspace_id", "state", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_manager_agent_id",
        "tasks",
        ["workspace_id", "manager_agent_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_created_at",
        "tasks",
        ["workspace_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "task_tags",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("tag_type", sa.String(64), nullable=False),
        sa.Column("tag", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "task_id", "tag_type", "tag"),
    )
    op.create_index(
        "ix_task_tags_reverse",
        "task_tags",
        ["workspace_id", "tag_type", "tag", "task_id"],
        unique=False,
    )

    op.create_table(
        "task_events",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("task_id", Uuid16(), nullable=True),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=True),
        sa.Column("source", sa.String(256), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.LargeBinary(), nullable=True),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("selected_routing_attempt_id", Uuid16(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.Column("routed_at", sa.Integer(), nullable=True),
        sa.Column("processed_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("state IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)", name="ck_task_events_state"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_task_events_task_state",
        "task_events",
        ["workspace_id", "task_id", "state", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_events_state_created",
        "task_events",
        ["workspace_id", "state", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_events_event_type",
        "task_events",
        ["workspace_id", "event_type", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_events_awaiting_user_selection",
        "task_events",
        ["workspace_id", "state", "updated_at", "id"],
        unique=False,
    )

    op.create_index(
        "ix_task_events_awaiting_grouping",
        "task_events",
        ["workspace_id", "state", "updated_at", "id"],
        unique=False,
    )

    op.create_table(
        "task_event_tags",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("event_id", Uuid16(), nullable=False),
        sa.Column("tag_type", sa.String(64), nullable=False),
        sa.Column("tag", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "event_id", "tag_type", "tag"),
    )
    op.create_index(
        "ix_task_event_tags_reverse",
        "task_event_tags",
        ["workspace_id", "tag_type", "tag", "event_id"],
        unique=False,
    )

    op.create_table(
        "task_event_routing_attempts",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("event_id", Uuid16(), nullable=False),
        sa.Column("candidate_task_id", Uuid16(), nullable=False),
        sa.Column("candidate_manager_agent_id", Uuid16(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("decision", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("manager_reason", sa.LargeBinary(), nullable=True),
        sa.Column("proposed_at", sa.Integer(), nullable=False),
        sa.Column("responded_at", sa.Integer(), nullable=True),
        sa.Column("selected_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "decision IN (1, 2, 3, 4, 5)",
            name="ck_task_event_routing_attempts_decision",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_task_event_routing_attempts_event",
        "task_event_routing_attempts",
        ["workspace_id", "event_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_event_routing_attempts_event_decision",
        "task_event_routing_attempts",
        ["workspace_id", "event_id", "decision", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_event_routing_attempts_candidate_task",
        "task_event_routing_attempts",
        ["workspace_id", "candidate_task_id", "event_id"],
        unique=False,
    )

    op.create_table(
        "task_event_routing_resolutions",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("event_id", Uuid16(), nullable=False),
        sa.Column("selected_attempt_id", Uuid16(), nullable=False),
        sa.Column("selected_task_id", Uuid16(), nullable=False),
        sa.Column("selected_manager_agent_id", Uuid16(), nullable=False),
        sa.Column("resolved_by_user_id", sa.String(128), nullable=True),
        sa.Column("resolution_note", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_task_event_routing_resolutions_event",
        "task_event_routing_resolutions",
        ["workspace_id", "event_id", "id"],
        unique=False,
    )

    op.create_table(
        "task_event_executions",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("task_item_id", Uuid16(), nullable=False),
        sa.Column("event_id", Uuid16(), nullable=True),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("manager_agent_id", Uuid16(), nullable=False),
        sa.Column("worker_agent_id", Uuid16(), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=True),
        sa.Column("status", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("assigned_at", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Integer(), nullable=True),
        sa.Column("finished_at", sa.Integer(), nullable=True),
        sa.Column("result_summary", sa.LargeBinary(), nullable=True),
        sa.Column("error", sa.LargeBinary(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN (1, 2, 3, 4, 5)",
            name="ck_task_event_executions_status",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_task_event_executions_task_item",
        "task_event_executions",
        ["workspace_id", "task_item_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_event_executions_event",
        "task_event_executions",
        ["workspace_id", "event_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_event_executions_task",
        "task_event_executions",
        ["workspace_id", "task_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_event_executions_worker",
        "task_event_executions",
        ["workspace_id", "worker_agent_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_event_executions_event_status",
        "task_event_executions",
        ["workspace_id", "event_id", "status", "id"],
        unique=False,
    )

    op.create_table(
        "task_items",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("canonical_key", sa.String(256), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("instructions", sa.LargeBinary(), nullable=True),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("worker_agent_id", Uuid16(), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("host_id", sa.String(64), nullable=True),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column("harness", sa.String(64), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(32), nullable=False, server_default="manager"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("state IN (1, 2, 3, 4, 5, 6, 7)", name="ck_task_items_state"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_task_items_task_state",
        "task_items",
        ["workspace_id", "task_id", "state", "id"],
        unique=False,
    )
    op.create_index(
        "ix_task_items_task_canonical_key",
        "task_items",
        ["workspace_id", "task_id", "canonical_key"],
        unique=False,
    )

    op.create_table(
        "task_item_events",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("task_item_id", Uuid16(), nullable=False),
        sa.Column("event_id", Uuid16(), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False, server_default="triggered"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "task_item_id", "event_id"),
    )
    op.create_index(
        "ix_task_item_events_event",
        "task_item_events",
        ["workspace_id", "event_id", "task_item_id"],
        unique=False,
    )

    op.create_table(
        "grouping_proposals",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("owner_user_id", sa.String(128), nullable=False),
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("resolved_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("state IN (1, 2, 3)", name="ck_grouping_proposals_state"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_grouping_proposals_owner_state",
        "grouping_proposals",
        ["workspace_id", "owner_user_id", "state", "id"],
        unique=False,
    )

    op.create_table(
        "grouping_proposal_events",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("proposal_id", Uuid16(), nullable=False),
        sa.Column("event_id", Uuid16(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "proposal_id", "event_id"),
    )


def downgrade() -> None:
    """Drop task routing and execution tables."""
    op.drop_table("grouping_proposal_events")
    op.drop_index("ix_grouping_proposals_owner_state", table_name="grouping_proposals")
    op.drop_table("grouping_proposals")
    op.drop_index("ix_task_item_events_event", table_name="task_item_events")
    op.drop_table("task_item_events")
    op.drop_index("ix_task_items_task_canonical_key", table_name="task_items")
    op.drop_index("ix_task_items_task_state", table_name="task_items")
    op.drop_table("task_items")
    op.drop_index(
        "ix_task_event_executions_event_status",
        table_name="task_event_executions",
    )
    op.drop_index("ix_task_event_executions_worker", table_name="task_event_executions")
    op.drop_index("ix_task_event_executions_task", table_name="task_event_executions")
    op.drop_index("ix_task_event_executions_event", table_name="task_event_executions")
    op.drop_index("ix_task_event_executions_task_item", table_name="task_event_executions")
    op.drop_table("task_event_executions")

    op.drop_index(
        "ix_task_event_routing_resolutions_event",
        table_name="task_event_routing_resolutions",
    )
    op.drop_table("task_event_routing_resolutions")

    op.drop_index(
        "ix_task_event_routing_attempts_candidate_task",
        table_name="task_event_routing_attempts",
    )
    op.drop_index(
        "ix_task_event_routing_attempts_event_decision",
        table_name="task_event_routing_attempts",
    )
    op.drop_index(
        "ix_task_event_routing_attempts_event",
        table_name="task_event_routing_attempts",
    )
    op.drop_table("task_event_routing_attempts")

    op.drop_index("ix_task_event_tags_reverse", table_name="task_event_tags")
    op.drop_table("task_event_tags")

    op.drop_index("ix_task_events_awaiting_grouping", table_name="task_events")
    op.drop_index(
        "ix_task_events_awaiting_user_selection",
        table_name="task_events",
    )
    op.drop_index("ix_task_events_event_type", table_name="task_events")
    op.drop_index("ix_task_events_state_created", table_name="task_events")
    op.drop_index("ix_task_events_task_state", table_name="task_events")
    op.drop_table("task_events")

    op.drop_index("ix_task_tags_reverse", table_name="task_tags")
    op.drop_table("task_tags")

    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_manager_agent_id", table_name="tasks")
    op.drop_index("ix_tasks_state_updated", table_name="tasks")
    op.drop_table("tasks")
