"""Board search: rank the user's live tasks by the query words their cards show.

A query word hits a task when its card text holds the word anywhere: the
title, goal, overview, task id, asset titles and URLs, and worker labels.
Each distinct word counts once per task, however often the
card holds it, so a query of two words hits a task at most twice. A task that
hits more words comes first; among tasks with the same count, the task active
most recently comes first. Chat content is not searched.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from omnigent.entities import Task, TaskAsset, Worker

#: The most distinct words a query uses.
MAX_QUERY_WORDS = 20


def query_words(query: str) -> list[str]:
    """Return the query's distinct lowercase words, in the order they first appear."""
    return list(dict.fromkeys(query.lower().split()))[:MAX_QUERY_WORDS]


@dataclass(frozen=True)
class BoardMatch:
    """A task whose card holds at least one query word.

    :param task: The matching task.
    :param hits: How many distinct query words the card holds.
    :param last_active: The latest of the task's last change and its workers'
        session activity, in Unix epoch seconds.
    :param asset_ids: The task's assets whose title or URL holds a query word.
    :param worker_ids: The task's workers whose label holds a query word.
    """

    task: Task
    hits: int
    last_active: int
    asset_ids: tuple[str, ...]
    worker_ids: tuple[str, ...]


def search_board(
    tasks: Sequence[Task],
    words: Sequence[str],
    *,
    assets: Sequence[TaskAsset],
    workers: Sequence[Worker],
    worker_labels: Mapping[str, str],
    worker_activity: Mapping[str, int],
) -> list[BoardMatch]:
    """
    Return the tasks whose cards hold any of ``words``, best match first.

    A task with more distinct words comes first, then the one active most
    recently, then the one higher on the board.

    :param tasks: The user's live tasks.
    :param words: Distinct lowercase query words, from :func:`query_words`.
    :param assets: The assets of those tasks.
    :param workers: The workers that the cards show.
    :param worker_labels: Each worker's label on its card, by worker id.
    :param worker_activity: When each worker's session last changed, by worker id.
    :returns: One match per task that holds a query word.
    """
    assets_by_task: defaultdict[str, list[TaskAsset]] = defaultdict(list)
    for asset in assets:
        assets_by_task[asset.task_id].append(asset)
    workers_by_task: defaultdict[str, list[Worker]] = defaultdict(list)
    for worker in workers:
        workers_by_task[worker.task_id].append(worker)

    matches: list[BoardMatch] = []
    for task in tasks:
        task_assets = assets_by_task[task.id]
        task_workers = workers_by_task[task.id]
        asset_texts = {asset.id: f"{asset.title}\n{asset.url}".lower() for asset in task_assets}
        worker_texts = {
            worker.id: worker_labels.get(worker.id, "").lower() for worker in task_workers
        }
        texts = [
            *(text.lower() for text in (task.title, task.goal, task.description or "", task.id)),
            *asset_texts.values(),
            *worker_texts.values(),
        ]
        hits = sum(1 for word in words if any(word in text for text in texts))
        if not hits:
            continue
        activity = [task.updated_at or task.created_at]
        activity.extend(worker_activity.get(worker.id, 0) for worker in task_workers)
        matches.append(
            BoardMatch(
                task=task,
                hits=hits,
                last_active=max(activity),
                asset_ids=tuple(
                    asset_id for asset_id, text in asset_texts.items() if _holds_any(text, words)
                ),
                worker_ids=tuple(
                    worker_id
                    for worker_id, text in worker_texts.items()
                    if _holds_any(text, words)
                ),
            )
        )
    matches.sort(
        key=lambda match: (-match.hits, -match.last_active, -match.task.queue_rank, match.task.id)
    )
    return matches


def _holds_any(text: str, words: Sequence[str]) -> bool:
    return any(word in text for word in words)
