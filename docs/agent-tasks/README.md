# PuppyGarden Task System

This is a task system whose goal is to create an immersive working environment, it tries to pull all the context/events instead of having user feed it.

## Roles/Components
* Router: a score based routing program, if there is clear winner, route the event to the corresponding task and let manager handle it
* Manager: Manager and task is 1:1 mapping, manager receives events, reconcile into taskItems and select a worker to assign it, make it a proposal for user to review
* Worker: works on taskItems, no special duty right now.
* Broker: when router cannot pick the winner, surface to broker, which will route to an active task or create new task for the event. While the task does not have active manager, broker also take some duty of manager: it reconcile the event into taskItems, merge/split taskItems, manage the tasks.

## Conecepts

* TaskEvent: like raw events, for example a slack message, a pr comment, a doc mention
* TaskItem: TaskEvents are just raw events, they got reconcile into TaskItem, for example multiple comment on same pr reconcile to same TaskItem, which is the execution unit containing instructions that actually get executed by workers.
* Task: the grouping unit, it has the goal which manager should steer towards to. it also has richest info so usually the system need to first find the right task for the taskEvent, then reconcile into an actual taskItem. Active task means there is already a manager spin up, pending task means this is just suggestion yet, it's still managed by broker
