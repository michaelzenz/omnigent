#!/usr/bin/env python3
"""Reset agent-task board data and drop legacy grouping tables."""

from __future__ import annotations

import os
import sqlite3
import sys

DEFAULT_DB = os.path.expanduser("~/.omnigent/chat.db")

CLEAR_TABLES = [
    "fyi_cluster_events",
    "fyi_clusters",
    "grouping_proposal_events",
    "grouping_proposals",
    "task_item_events",
    "task_items",
    "task_event_executions",
    "task_event_routing_resolutions",
    "task_event_routing_attempts",
    "task_event_tags",
    "task_events",
    "task_session_bindings",
    "task_tags",
    "tasks",
]

DROP_LEGACY = [
    "DROP TABLE IF EXISTS grouping_proposal_events",
    "DROP TABLE IF EXISTS grouping_proposals",
]


def main() -> int:
    db_path = os.environ.get("OMNIGENT_DB_PATH", DEFAULT_DB)
    if not os.path.exists(db_path):
        print(f"database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        for statement in DROP_LEGACY:
            conn.execute(statement)
        for table in CLEAR_TABLES:
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        # Tighten task_events check constraint when legacy DB still allows state 5.
        try:
            conn.execute("ALTER TABLE task_events DROP CONSTRAINT ck_task_events_state")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()

    print(f"cleared agent-task data in {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
