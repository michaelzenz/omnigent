"""Subscriptions of managers to the sessions of their tasks' workers.

A manager's session follows each worker session of its live tasks as an
``omnigent.session`` source, so the server tells the manager when a worker's
turn ends. The functions block, and change nothing when the task has no
manager with a session. ``follow_workers`` also changes nothing for a task
that is not live.
"""

from __future__ import annotations

from collections.abc import Collection

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import LIVE_TASK_STATES, SESSION_EVENT_SOURCE, Task
from omnigent.stores.subscription_store import MAX_SUBSCRIPTION_NOTE_CHARS
from omnigent.util.uuid import normalize_uuid


def _manager_session_id(
    board: BoardStores, manager_id: str | None, owner_user_id: str | None
) -> str | None:
    if manager_id is None:
        return None
    manager = board.managers.get(manager_id, owner_user_id=owner_user_id)
    return manager.conversation_id if manager is not None else None


def follow_workers(
    board: BoardStores,
    task: Task,
    *,
    owner_user_id: str | None,
    session_ids: Collection[str] | None = None,
) -> None:
    """
    Subscribe the task's manager to the task's worker sessions, when the task is live.

    :param board: The board's tasks, managers, workers and subscriptions.
    :param task: The task.
    :param owner_user_id: The task's owner, or ``None`` in single-user mode.
    :param session_ids: Only these worker sessions; every worker of the task
        when ``None``.
    """
    if task.state not in LIVE_TASK_STATES:
        return
    manager_session_id = _manager_session_id(board, task.manager_id, owner_user_id)
    if manager_session_id is None:
        return
    if session_ids is None:
        session_ids = [
            worker.session_id
            for worker in board.workers.list_for_tasks([task.id], owner_user_id=owner_user_id)
        ]
    note = f"Worker on the task {task.title}"[:MAX_SUBSCRIPTION_NOTE_CHARS]
    for session_id in dict.fromkeys(normalize_uuid(key) or key for key in session_ids):
        if session_id == manager_session_id:
            continue
        board.subscriptions.subscribe(
            owner_user_id=owner_user_id,
            session_id=manager_session_id,
            source_type=SESSION_EVENT_SOURCE,
            source_key=session_id,
            note=note,
        )


def unfollow_workers(
    board: BoardStores, task: Task, *, owner_user_id: str | None, manager_id: str | None
) -> None:
    """
    Unsubscribe a manager from the task's worker sessions.

    A worker session that also works on another live task of the manager stays
    subscribed.

    :param board: The board's tasks, managers, workers and subscriptions.
    :param task: The task.
    :param owner_user_id: The task's owner, or ``None`` in single-user mode.
    :param manager_id: The manager that stops following the task's workers.
    """
    manager_session_id = _manager_session_id(board, manager_id, owner_user_id)
    if manager_session_id is None or manager_id is None:
        return
    worker_sessions = {
        worker.session_id
        for worker in board.workers.list_for_tasks([task.id], owner_user_id=owner_user_id)
    }
    if not worker_sessions:
        return
    other_tasks = [
        other.id
        for other in board.tasks.list(
            owner_user_id=owner_user_id, states=LIVE_TASK_STATES, manager_id=manager_id
        )
        if other.id != task.id
    ]
    kept = {
        worker.session_id
        for worker in board.workers.list_for_tasks(other_tasks, owner_user_id=owner_user_id)
    }
    for session_id in worker_sessions - kept:
        board.subscriptions.unsubscribe(
            owner_user_id=owner_user_id, session_id=manager_session_id, source_key=session_id
        )
