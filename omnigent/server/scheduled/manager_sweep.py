"""Manager task-sweep automation.

Every manager gets a daily scheduled task that injects a sweep prompt into
its existing session: re-read all managed tasks, refresh descriptions /
internal notes / items / worker titles, resolve settled tasks. This keeps
portfolios current without waiting for an event to arrive.

The automation is created when the manager is created and deleted with it.
It targets the manager's *durable* session — the fire path wakes/heals the
session through the same resolution the manager queue handler uses, so a
healed manager identity keeps its sweep schedule.

The created task's id is registered with the fire path's in-memory sweep
registry (``fire._MANAGER_SWEEP_TASK_IDS``) so a firing dispatches into the
manager's existing session instead of creating a fresh one. The registry is
rebuilt at server wiring from the store (see ``rebuild_sweep_registry``).
"""

from __future__ import annotations

import secrets
import uuid

from omnigent.stores.scheduled_task_store import ScheduledTaskStore

# Stable name prefix so an existing sweep is findable without a schema
# change (scheduled_tasks has no label column): deterministic per manager.
SWEEP_NAME_PREFIX = "puppygarden-manager-sweep:"

MANAGER_SWEEP_PROMPT = (
    "This is not event routing \u2014 please sweep all the tasks assigned to you: "
    "update each task's description and internal notes to reflect its current "
    "state, review and update or resolve its task items, update worker titles "
    "to what each worker has recently done, and resolve the task itself if "
    "appropriate, following the Manager manual."
)


def manager_sweep_task_name(manager_id: str) -> str:
    """The deterministic scheduled-task name for one manager's sweep."""
    return f"{SWEEP_NAME_PREFIX}{manager_id}"


def manager_id_from_sweep_name(name: str) -> str | None:
    """The manager id encoded in a sweep task name, or ``None``."""
    if name.startswith(SWEEP_NAME_PREFIX):
        return name[len(SWEEP_NAME_PREFIX) :]
    return None


def _random_daily_rrule() -> str:
    """A daily RRULE at a random hour/minute \u2014 spreads sweeps across the day."""
    return f"FREQ=DAILY;BYHOUR={secrets.randbelow(24)};BYMINUTE={secrets.randbelow(60)}"


def _register_sweep_task_id(scheduled_task_id: str) -> None:
    """Record a sweep task id with the fire path's in-memory sweep registry."""
    from omnigent.server.scheduled.fire import _MANAGER_SWEEP_TASK_IDS

    _MANAGER_SWEEP_TASK_IDS.add(scheduled_task_id)


def ensure_manager_sweep_task(
    *,
    manager_id: str,
    manager_conversation_id: str | None,
    manager_agent_id: str,
    owner_user_id: str | None,
    store: ScheduledTaskStore,
) -> str | None:
    """Create the daily sweep automation for a manager, if not already present.

    Idempotent: an existing active sweep for this manager is returned
    unchanged (and re-registered with the fire path's registry). The task
    carries the manager's durable agent binding and no host/workspace pin \u2014
    at fire time the runner resolves the owner's online host and the manager
    session is re-created from its own snapshot if the pointer went stale.

    :returns: The scheduled-task id, or ``None`` when the manager's session
        is unknown (nothing to invoke).
    """
    if manager_conversation_id is None:
        return None
    name = manager_sweep_task_name(manager_id)
    for task in store.list(owner_user_id=owner_user_id):
        if task.name == name and task.state == "active":
            _register_sweep_task_id(task.id)
            return task.id

    task = store.create(
        scheduled_task_id=uuid.uuid4().hex,
        name=name,
        prompt=MANAGER_SWEEP_PROMPT,
        rrule=_random_daily_rrule(),
        user_id=owner_user_id,
        agent_id=manager_agent_id,
        timezone="UTC",
        # No host/workspace pin: the fire path resolves the owner's online
        # host and the manager session heals from its row snapshot.
        workspace=None,
        host_id=None,
        state="active",
        catch_up=True,
    )
    _register_sweep_task_id(task.id)
    return task.id


def rebuild_sweep_registry(*, owner_user_id: str | None, store: ScheduledTaskStore) -> int:
    """Re-register every active sweep task with the fire path's registry.

    ``fire._MANAGER_SWEEP_TASK_IDS`` is process-local, so a server boot starts
    cold; without this rebuild a sweep fire would fail the id check and fall
    through to the default scheduled-task path — creating a fresh session per
    firing instead of prompting the manager's existing one. Sweeps carry no
    schema marker, so the rebuild scans for the deterministic name prefix.

    :returns: How many sweep tasks were (re-)registered.
    """
    from omnigent.server.scheduled.fire import _MANAGER_SWEEP_TASK_IDS

    count = 0
    for task in store.list(owner_user_id=owner_user_id):
        if task.state == "active" and manager_id_from_sweep_name(task.name) is not None:
            _MANAGER_SWEEP_TASK_IDS.add(task.id)
            count += 1
    return count


def delete_manager_sweep_task(
    *,
    manager_id: str,
    owner_user_id: str | None,
    store: ScheduledTaskStore,
) -> str | None:
    """Delete a manager's sweep automation. Returns the deleted task id."""
    from omnigent.server.scheduled.fire import _MANAGER_SWEEP_TASK_IDS

    name = manager_sweep_task_name(manager_id)
    for task in store.list(owner_user_id=owner_user_id):
        if task.name == name:
            if store.delete(task.id):
                _MANAGER_SWEEP_TASK_IDS.discard(task.id)
                return task.id
    return None
