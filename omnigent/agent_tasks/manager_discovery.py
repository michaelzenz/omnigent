"""Manager discovery for the attach flow and the broker distributor.

Lists the owner's active managers with their task portfolios and capacity
(`GET /v1/managers`), and picks an existing manager to attach a task to.
Host compatibility is a correctness filter: an event from a session on host A
must not land on a manager on host B; workspace is relaxed.
"""

from __future__ import annotations

from dataclasses import dataclass

from omnigent.agent_tasks.constants import MANAGER_TASK_CAPACITY
from omnigent.agent_tasks.task_search import score_task_text
from omnigent.entities import Task
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_store import TaskStore

# A live task occupies capacity on its manager. Archived tasks free the slot.
_LIVE_TASK_STATES = frozenset({"active", "idle", "pending"})


@dataclass
class ManagerInfo:
    """One active manager session and the tasks it owns."""

    conversation_id: str
    host_id: str | None
    workspace: str | None
    role_key: str | None
    title: str | None
    tasks: list[Task]

    @property
    def task_count(self) -> int:
        return len(self.tasks)


def list_active_managers(
    *,
    owner_user_id: str,
    task_store: TaskStore,
    conversation_store: ConversationStore,
) -> list[ManagerInfo]:
    """Return manager sessions owning ≥1 of the owner's tasks, with portfolios.

    A manager's portfolio counts every task on it — including other owners',
    when a session is shared — since that is the real load against capacity.
    """
    managers: list[ManagerInfo] = []
    for conversation_id in task_store.list_manager_conversation_ids(
        owner_user_id=owner_user_id
    ):
        conv = conversation_store.get_conversation(conversation_id)
        if conv is None:
            continue
        tasks = [
            task
            for task in task_store.list_by_manager_conversation_id(conversation_id)
            if task.state in _LIVE_TASK_STATES
        ]
        managers.append(
            ManagerInfo(
                conversation_id=conversation_id,
                host_id=conv.host_id,
                workspace=conv.workspace,
                role_key=None,
                title=conv.title,
                tasks=tasks,
            )
        )
    return managers


def choose_manager_for_task(
    managers: list[ManagerInfo],
    *,
    probe: Task,
    host_id: str | None,
    capacity: int = MANAGER_TASK_CAPACITY,
) -> ManagerInfo | None:
    """Pick the best existing manager for ``probe``, or ``None`` to spawn a new one.

    Filters to host-compatible managers with capacity left, then ranks by text
    relevance between the probe task and each manager's portfolio. The best
    portfolio score wins; ties fall to the first compatible manager (attach
    decisions are logged so a real threshold can be tuned later).
    """
    candidates = [
        manager
        for manager in managers
        if manager.task_count < capacity
        and (host_id is None or manager.host_id is None or manager.host_id == host_id)
    ]
    if not candidates:
        return None
    scored: list[tuple[ManagerInfo, float]] = []
    for manager in candidates:
        score = max(
            (score_task_text(owned, query=_probe_text(probe)) for owned in manager.tasks),
            default=0.0,
        )
        scored.append((manager, score))
    scored.sort(key=lambda row: (-row[1], row[0].conversation_id))
    return scored[0][0]


def _probe_text(task: Task) -> str:
    """The text a new task probes manager portfolios with."""
    return " ".join(part for part in [task.title, task.goal] if part)


def manager_role_profile_response(
    profiles: list[TaskRoleProfile],
) -> list[dict[str, object]]:
    """Serialize role profiles the broker can spawn a new manager from."""
    return [
        {
            "role": profile.role,
            "host_id": profile.host_id,
            "workspace": profile.workspace,
            "harness": profile.harness,
            "model": profile.model,
            "description": profile.description,
        }
        for profile in profiles
    ]
