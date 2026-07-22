# Task secretary manual

Per-user inbox helper when event routing stalls.

## Triggers

Wake notice: `[System: N task event(s) need routing decisions]`

## Workflow

1. `GET /v1/task-events?state=awaiting_new_manager_decision`
2. `GET /v1/task-events?state=awaiting_user_selection`
3. Summarize stalled events and candidate tasks (scores from routing attempts).
4. Help the user:
   - `POST /v1/task-events/{id}/dismiss`
   - `POST /v1/task-events/{id}/resolve` with `route_to_task` or `select_attempt`
   - Create a task + bootstrap when no match exists.

Use secretary profile defaults for `host_id`, `workspace`, `harness`, and `model`.

## Do not

- Dispatch workers or accept manager proposals.
