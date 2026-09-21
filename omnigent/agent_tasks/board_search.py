"""Board search: match a query against a task's board-visible text.

Powers ``GET /v1/agent-tasks/board-search`` — the PuppyGarden board's
floating search bar. Server-side only: the board fetches matching task ids
plus per-entity match ids (items, assets, workers) and renders rings from
them. Executions, the manager conversation, and worker chat content are
out of scope — the search targets the task's rendered content and its
workers' lane text.
"""

from __future__ import annotations

from typing import Any

from omnigent.entities import Task, TaskAsset, TaskItem, Worker


def _contains(haystack: str | None, needle: str) -> bool:
    return bool(haystack) and needle in haystack.lower()


def _item_texts(item: TaskItem) -> tuple[str | None, str | None, str | None]:
    return (item.title, item.description, item.instructions)


def _worker_matches_query(worker: Worker, needle: str) -> bool:
    """Whether the worker's board-visible lane text matches — exactly the
    fields the Workers tab renders: title, provider name, failure reason."""
    return (
        _contains(worker.title, needle)
        or _contains(worker.provider_name, needle)
        or _contains(worker.failure_reason, needle)
    )


def search_board_tasks(
    tasks: list[Task],
    *,
    query: str,
    items: list[TaskItem],
    workers: list[Worker],
    assets: list[TaskAsset],
) -> list[dict[str, Any]]:
    """Match ``query`` against the board-visible text of ``tasks``.

    Everything — task fields, items, assets, worker lane text — is matched
    against the loaded rows. Chat content is not probed.

    :param tasks: The caller's visible tasks (ACL + state filtering already
        applied; archived tasks excluded).
    :param query: The normalized (lowercased) search query.
    :param items: Task items across all candidate tasks.
    :param workers: Workers across all candidate tasks (terminated/deleted
        excluded — they are untracked and never rendered).
    :param assets: Assets across all candidate tasks.
    :returns: One result dict per matching task:
        ``{"task_id", "matched_in", "item_ids", "asset_ids", "worker_ids"}``
        where ``matched_in`` is a subset of ``task|item|asset|worker``.
    """
    needle = query.strip().lower()
    if not needle:
        return []

    items_by_task: dict[str, list[TaskItem]] = {}
    for item in items:
        items_by_task.setdefault(item.task_id, []).append(item)

    workers_by_task: dict[str, list[Worker]] = {}
    for worker in workers:
        workers_by_task.setdefault(worker.task_id, []).append(worker)

    assets_by_task: dict[str, list[TaskAsset]] = {}
    for asset in assets:
        assets_by_task.setdefault(asset.task_id, []).append(asset)

    results: list[dict[str, Any]] = []
    for task in tasks:
        matched_in: set[str] = set()
        task_items = items_by_task.get(task.id, [])
        task_workers = workers_by_task.get(task.id, [])
        task_assets = assets_by_task.get(task.id, [])

        if (
            _contains(task.title, needle)
            or _contains(task.goal, needle)
            or _contains(task.description, needle)
            or _contains(task.id, needle)
        ):
            matched_in.add("task")

        item_ids = [
            item.id
            for item in task_items
            if any(_contains(text, needle) for text in _item_texts(item))
        ]
        if item_ids:
            matched_in.add("item")

        asset_ids = [
            asset.id
            for asset in task_assets
            if _contains(asset.title, needle) or _contains(asset.url, needle)
        ]
        if asset_ids:
            matched_in.add("asset")

        worker_ids = {
            worker.id for worker in task_workers if _worker_matches_query(worker, needle)
        }
        if worker_ids:
            matched_in.add("worker")

        if not matched_in:
            continue
        results.append(
            {
                "task_id": task.id,
                "matched_in": sorted(matched_in),
                "item_ids": item_ids,
                "asset_ids": asset_ids,
                "worker_ids": sorted(worker_ids),
            }
        )
    return results
