#!/usr/bin/env python3
"""One-time migration: re-key manager queues from task scope to manager scope.

Cancels every open manager-role queue item (task-scoped, pre-v2) and flips its
reconciled source events back to ``routed`` so the manager packager
re-packages them under the new ``manager/<owner>/<manager_conversation_id>``
keys. In-flight items complete naturally via the status feed.

Usage:

    python scripts/rekey_manager_queues.py            # dry run
    python scripts/rekey_manager_queues.py --apply    # perform the migration
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnigent.agent_tasks.queue.rekey_migration import rekey_manager_queues
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore

DEFAULT_DB = os.path.expanduser("~/.omnigent/chat.db")


def main() -> int:
    db_path = os.environ.get("OMNIGENT_DB_PATH", DEFAULT_DB)
    if not os.path.exists(db_path):
        print(f"database not found: {db_path}", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv
    db_uri = f"sqlite:///{db_path}"
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)

    if not apply:
        from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_ROLE
        from omnigent.agent_tasks.queue.rekey_migration import _CANCELABLE_STATES

        open_items = queue_store.list_open_items_for_role(TASK_MANAGER_ROLE)
        cancelable = sum(1 for i in open_items if i.state in _CANCELABLE_STATES)
        print(
            f"dry run: {cancelable} open manager queue items would be cancelled "
            f"({len(open_items) - cancelable} in flight, untouched). Re-run with --apply."
        )
        return 0

    result = rekey_manager_queues(
        agent_queue_store=queue_store,
        task_event_store=event_store,
    )
    print(
        f"rekey complete: {result['items_canceled']} items cancelled, "
        f"{result['events_requeued']} events back to routed, "
        f"{result['items_in_flight']} in flight (complete naturally)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
