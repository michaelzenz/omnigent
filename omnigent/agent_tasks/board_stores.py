"""The stores behind each user's GlobalHub board."""

from __future__ import annotations

from dataclasses import dataclass

from omnigent.stores.manager_store import ManagerStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store import WorkerStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


@dataclass(frozen=True)
class BoardStores:
    """
    The stores behind each user's GlobalHub board.

    :param tasks: The board's tasks.
    :param managers: The managers that own the tasks.
    :param workers: The sessions working on the tasks.
    :param events: The signals the broker routes to managers.
    """

    tasks: TaskStore
    managers: ManagerStore
    workers: WorkerStore
    events: TaskEventStore


def open_board_stores(conversation_store: object) -> BoardStores | None:
    """
    Open the board stores beside the session metadata, or ``None`` without SQL.

    :param conversation_store: The server's conversation store; the board lives
        in the database at its ``storage_location``.
    """
    location = getattr(conversation_store, "storage_location", None)
    if not isinstance(location, str) or not location:
        return None
    return BoardStores(
        tasks=SqlAlchemyTaskStore(location),
        managers=SqlAlchemyManagerStore(location),
        workers=SqlAlchemyWorkerStore(location),
        events=SqlAlchemyTaskEventStore(location),
    )
