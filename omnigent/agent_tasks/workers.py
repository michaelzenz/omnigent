"""Worker lookup helpers for managed task items."""

from __future__ import annotations

import uuid

from omnigent.entities import TaskItem, Worker
from omnigent.entities.conversation import Conversation
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.worker_store import WorkerStore


def _generate_worker_id() -> str:
    """Return a durable PuppyGarden Worker ID."""
    return uuid.uuid4().hex


def worker_for_item(item: TaskItem, *, worker_store: WorkerStore) -> Worker | None:
    """Return the durable Worker assigned to an item, if any."""
    if item.worker_id is None:
        return None
    return worker_store.get_worker(item.worker_id)


def worker_last_active_at(worker: Worker, conversations: dict[str, Conversation]) -> int:
    """Last-active epoch seconds for a worker lane.

    Adopted/managed lanes mirror their target session's ``updated_at``
    (bumped on every item append — the same signal that orders the
    sidebar). External lanes have no local conversation, so the watcher's
    ``last_observed_at`` is the freshest signal; the worker row's own
    timestamps cover lanes that never ran.
    """
    conv = conversations.get(worker.target_id) if worker.target_id is not None else None
    if conv is not None:
        return conv.updated_at
    return worker.last_observed_at or worker.updated_at or worker.created_at


def worker_last_active_map(
    workers: list[Worker],
    conversation_store: ConversationStore | None,
) -> dict[str, int]:
    """Bulk last-active lookup — one conversation round-trip for all lanes."""
    target_ids = [worker.target_id for worker in workers if worker.target_id is not None]
    conversations = (
        conversation_store.get_conversations(target_ids) if conversation_store is not None else {}
    )
    return {worker.id: worker_last_active_at(worker, conversations) for worker in workers}
