"""Board search: match a query against a task's board-visible text.

Powers ``GET /v1/agent-tasks/board-search`` — the pmv2 board's
floating search bar. Server-side only: the board fetches matching task ids
plus per-entity match ids (items, assets, workers) and renders rings from
them. Executions, the manager conversation, and worker chat content are
out of scope — the search targets the task's rendered content and its
workers' lane text.

Matching is token-AND: the query is lowercased and split on whitespace,
and every token must match somewhere in a task's board-visible text (any
field, any order — ``fix auth`` matches a task titled ``Fix the auth
timeout``). Results are ranked best match first: exact phrase hits and full
footprint-in-title matches earn bonuses, and important fields (titles)
outweigh descriptions and failure text — see the weight and bonus constants
below.
"""

from __future__ import annotations

from typing import Any

from omnigent.entities import Task, TaskAsset, TaskItem, Worker

# Field importance for score ranking (arbitrary 0-100 scale). Titles rank
# above descriptions, and task/item fields above worker failure text.
_TASK_FIELD_WEIGHTS = {"title": 100, "goal": 80, "description": 60, "id": 40}
_ITEM_FIELD_WEIGHTS = {"title": 90, "description": 55, "instructions": 45}
_ASSET_FIELD_WEIGHTS = {"title": 70, "url": 30}
_WORKER_FIELD_WEIGHTS = {"title": 85, "provider_name": 65, "failure_reason": 35}

# An exact phrase hit beats a token-spread match, and a task whose title
# alone covers every token beats one that needs other entities.
_PHRASE_BONUS = 300
_TITLE_COMPLETE_BONUS = 200

# Lowercase (weight, text) pairs for one entity's board-visible fields.
_FieldList = list[tuple[int, str]]


def tokenize_query(query: str) -> list[str]:
    """Lowercase the query and split on whitespace runs; empty tokens dropped."""
    return [token for token in query.strip().lower().split() if token]


def _task_fields(task: Task) -> _FieldList:
    return [
        (_TASK_FIELD_WEIGHTS["title"], (task.title or "").lower()),
        (_TASK_FIELD_WEIGHTS["goal"], (task.goal or "").lower()),
        (_TASK_FIELD_WEIGHTS["description"], (task.description or "").lower()),
        (_TASK_FIELD_WEIGHTS["id"], task.id.lower()),
    ]


def _item_fields(item: TaskItem) -> _FieldList:
    return [
        (_ITEM_FIELD_WEIGHTS["title"], (item.title or "").lower()),
        (_ITEM_FIELD_WEIGHTS["description"], (item.description or "").lower()),
        (_ITEM_FIELD_WEIGHTS["instructions"], (item.instructions or "").lower()),
    ]


def _asset_fields(asset: TaskAsset) -> _FieldList:
    return [
        (_ASSET_FIELD_WEIGHTS["title"], (asset.title or "").lower()),
        (_ASSET_FIELD_WEIGHTS["url"], (asset.url or "").lower()),
    ]


def _worker_fields(worker: Worker) -> _FieldList:
    """Exactly the fields the Workers tab renders: title, provider name,
    failure reason."""
    return [
        (_WORKER_FIELD_WEIGHTS["title"], (worker.title or "").lower()),
        (_WORKER_FIELD_WEIGHTS["provider_name"], (worker.provider_name or "").lower()),
        (_WORKER_FIELD_WEIGHTS["failure_reason"], (worker.failure_reason or "").lower()),
    ]


def _any_token(fields: _FieldList, tokens: list[str]) -> bool:
    return any(token in text for token in tokens for _, text in fields)


def _score_task(
    task: Task,
    tokens: list[str],
    phrase: str,
    task_items: list[TaskItem],
    task_workers: list[Worker],
    task_assets: list[TaskAsset],
) -> int | None:
    """Score one task against the query tokens, or None when it does not
    match (some token matches nowhere).

    Score = sum over tokens of the weight of the most important field that
    token hits, plus a bonus for an exact phrase hit and for covering every
    token with the task title alone.
    """
    all_fields = _task_fields(task)
    for item in task_items:
        all_fields.extend(_item_fields(item))
    for worker in task_workers:
        all_fields.extend(_worker_fields(worker))
    for asset in task_assets:
        all_fields.extend(_asset_fields(asset))

    score = 0
    for token in tokens:
        best = max((weight for weight, text in all_fields if token in text), default=0)
        if best == 0:
            return None
        score += best

    if any(phrase in text for _, text in all_fields):
        score += _PHRASE_BONUS
    title = (task.title or "").lower()
    if title and all(token in title for token in tokens):
        score += _TITLE_COMPLETE_BONUS
    return score


def search_board_tasks(
    tasks: list[Task],
    *,
    query: str,
    items: list[TaskItem],
    workers: list[Worker],
    assets: list[TaskAsset],
) -> list[dict[str, Any]]:
    """Match ``query`` against the board-visible text of ``tasks``.

    Token-AND: every whitespace-split, lowercased token must match somewhere
    within the task's board-visible text (task fields, items, assets, worker
    lane text). Chat content is not probed.

    :param tasks: The caller's visible tasks (ACL + state filtering already
        applied; archived tasks excluded).
    :param query: The raw search query (tokenized here).
    :param items: Task items across all candidate tasks.
    :param workers: Workers across all candidate tasks (terminated/deleted
        excluded — they are untracked and never rendered).
    :param assets: Assets across all candidate tasks.
    :returns: One result dict per matching task, best match first:
        ``{"task_id", "matched_in", "item_ids", "asset_ids", "worker_ids",
        "score"}`` where ``matched_in`` is a subset of ``task|item|asset|
        worker`` and an entity id is included when any query token matches
        its text. Ties keep the caller's task order.
    """
    tokens = tokenize_query(query)
    if not tokens:
        return []
    phrase = " ".join(tokens)

    items_by_task: dict[str, list[TaskItem]] = {}
    for item in items:
        items_by_task.setdefault(item.task_id, []).append(item)

    workers_by_task: dict[str, list[Worker]] = {}
    for worker in workers:
        workers_by_task.setdefault(worker.task_id, []).append(worker)

    assets_by_task: dict[str, list[TaskAsset]] = {}
    for asset in assets:
        assets_by_task.setdefault(asset.task_id, []).append(asset)

    scored: list[tuple[int, dict[str, Any]]] = []
    for task in tasks:
        task_items = items_by_task.get(task.id, [])
        task_workers = workers_by_task.get(task.id, [])
        task_assets = assets_by_task.get(task.id, [])

        score = _score_task(task, tokens, phrase, task_items, task_workers, task_assets)
        if score is None:
            continue

        matched_in: set[str] = set()
        if _any_token(_task_fields(task), tokens):
            matched_in.add("task")
        item_ids = [
            item.id for item in task_items if _any_token(_item_fields(item), tokens)
        ]
        if item_ids:
            matched_in.add("item")
        asset_ids = [
            asset.id for asset in task_assets if _any_token(_asset_fields(asset), tokens)
        ]
        if asset_ids:
            matched_in.add("asset")
        worker_ids = sorted(
            worker.id for worker in task_workers if _any_token(_worker_fields(worker), tokens)
        )
        if worker_ids:
            matched_in.add("worker")

        scored.append(
            (
                score,
                {
                    "task_id": task.id,
                    "matched_in": sorted(matched_in),
                    "item_ids": item_ids,
                    "asset_ids": asset_ids,
                    "worker_ids": worker_ids,
                    "score": score,
                },
            )
        )

    # Best match first; sort is stable so ties keep the caller's task order.
    scored.sort(key=lambda entry: -entry[0])
    return [result for _, result in scored]
