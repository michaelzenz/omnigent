# PuppyGarden Task System

This is a task system whose goal is to create an immersive working environment

## Conecepts

* TaskEvent: like raw events, for example a slack message, a pr comment, a doc mention
* TaskItem: TaskEvents got reconcile into TaskItem, for example multiple comment on same pr reconcile to same TaskItem. This is the execution unit.
* Task: the grouping unit, multiple TaskItem is mapped to one Task.
