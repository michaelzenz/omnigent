"""The stores behind each user's GlobalHub board."""

from __future__ import annotations

from dataclasses import dataclass

from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.fyi_cluster_store import FyiClusterStore
from omnigent.stores.fyi_cluster_store.sqlalchemy_store import SqlAlchemyFyiClusterStore
from omnigent.stores.manager_store import ManagerStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.role_schedule_store import RoleScheduleStore
from omnigent.stores.role_schedule_store.sqlalchemy_store import SqlAlchemyRoleScheduleStore
from omnigent.stores.system_notice_store import SystemNoticeStore
from omnigent.stores.system_notice_store.sqlalchemy_store import SqlAlchemySystemNoticeStore
from omnigent.stores.task_asset_store import TaskAssetStore
from omnigent.stores.task_asset_store.sqlalchemy_store import SqlAlchemyTaskAssetStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.task_subscription_store import TaskSubscriptionStore
from omnigent.stores.task_subscription_store.sqlalchemy_store import (
    SqlAlchemyTaskSubscriptionStore,
)
from omnigent.stores.watch_item_store import WatchItemStore
from omnigent.stores.watch_item_store.sqlalchemy_store import SqlAlchemyWatchItemStore
from omnigent.stores.watcher_store import WatcherStore
from omnigent.stores.watcher_store.sqlalchemy_store import SqlAlchemyWatcherStore
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
    :param fyi_clusters: Events that need no task, kept for the user to read.
    :param assets: The links the tasks carry.
    :param system_notices: The notice pinned on each user's board.
    :param queue: The messages waiting for the role sessions.
    :param role_schedules: When each user's role sessions get their scheduled runs.
    :param watchers: The watchers that report changes in the things they watch.
    :param watch_items: What each watcher tracks, with what it saw last.
    :param subscriptions: The keys that the tasks follow, which route events.
    """

    tasks: TaskStore
    managers: ManagerStore
    workers: WorkerStore
    events: TaskEventStore
    fyi_clusters: FyiClusterStore
    assets: TaskAssetStore
    system_notices: SystemNoticeStore
    queue: AgentQueueStore
    role_schedules: RoleScheduleStore
    watchers: WatcherStore
    watch_items: WatchItemStore
    subscriptions: TaskSubscriptionStore


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
        fyi_clusters=SqlAlchemyFyiClusterStore(location),
        assets=SqlAlchemyTaskAssetStore(location),
        system_notices=SqlAlchemySystemNoticeStore(location),
        queue=SqlAlchemyAgentQueueStore(location),
        role_schedules=SqlAlchemyRoleScheduleStore(location),
        watchers=SqlAlchemyWatcherStore(location),
        watch_items=SqlAlchemyWatchItemStore(location),
        subscriptions=SqlAlchemyTaskSubscriptionStore(location),
    )
