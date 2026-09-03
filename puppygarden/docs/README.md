# PuppyGarden Task System

This is a task system whose goal is to create an immersive working environment, it tries to pull all the context/events instead of having user feed it.

## Finding the manuals

Resolve the data dir from `$OMNIGENT_DATA_DIR` (falling back to
`~/.omnigent` when unset), then read `host.puppygarden.root` from
`<data_dir>/config.yaml`. This directory is the PuppyGarden root; all
manuals are under its `docs/` directory:

```yaml
host:
  puppygarden:
    root: /path/to/omnigent/puppygarden
```

All manuals are inside <host.puppygarden.root>/docs/

## Manuals

* `POLL_PLUGINS.md` — host-side scripts that watch external signals on an interval and emit task events
* `API_REFERENCE.md` — REST API reference for the task system
* `TASK_BROKER.md` — broker role and task routing
* `TASK_MANAGER.md` — manager role and task item reconciliation
* `TASK_SECRETARY.md` — secretary role and plugin writing
* Automations — use `sys_scheduled_task_create` for recurring agent sessions (RRULE schedule, catch-up toggle, cost control)

## Roles/Components
* Router: a score-based routing program; when there is a clear winner, route the event to the corresponding task's manager
* Manager: a first-class, long-lived agent that owns a portfolio of tasks, maintains a routing description, reconciles events into taskItems, and selects workers
* Worker: works on taskItems, no special duty right now.
* Broker: when the router cannot pick a destination, list managers, select one by description, create one when none fits, and route the event. It never manages tasks or taskItems.

## Conecepts

* TaskEvent: like raw events, for example a slack message, a pr comment, a doc mention
* TaskItem: TaskEvents are just raw events, they got reconcile into TaskItem, for example multiple comment on same pr reconcile to same TaskItem, which is the execution unit containing instructions that actually get executed by workers.
* Task: the grouping unit with a goal that its manager steers toward. A manager chooses or creates the task after receiving an event, then reconciles the event into a taskItem. Active tasks are user-confirmed; pending tasks are manager proposals awaiting confirmation.
