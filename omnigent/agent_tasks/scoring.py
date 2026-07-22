"""Keyword confidence scoring for task-event routing."""

from __future__ import annotations

import re

from omnigent.agent_tasks.constants import (
    AUTO_ROUTE_MAX_CANDIDATES,
    AUTO_ROUTE_MIN_CONFIDENCE,
    AUTO_ROUTE_MIN_MARGIN,
)
from omnigent.entities import Task

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[:][a-z0-9_-]+)?", re.IGNORECASE)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)


def tokenize_search_text(text: str) -> list[str]:
    """Split searchable text into lowercase routing tokens."""
    tokens: list[str] = []
    for raw in text.split("\n"):
        for match in _TOKEN_RE.finditer(raw.lower()):
            token = match.group(0)
            if token in _STOPWORDS or len(token) < 2:
                continue
            tokens.append(token)
    return tokens


def score_task_for_event(*, event_tokens: list[str], task: Task) -> float:
    """
    Score how well a task matches event tokens.

    Returns the fraction of distinct event tokens found in the task search text.
    Tag-shaped tokens (``type:value``) count double when matched.
    """
    if not event_tokens:
        return 0.0
    haystack = task.search_text.lower()
    weight = 0.0
    total_weight = 0.0
    for token in event_tokens:
        token_weight = 2.0 if ":" in token else 1.0
        total_weight += token_weight
        if token in haystack:
            weight += token_weight
    if total_weight <= 0:
        return 0.0
    return weight / total_weight


def rank_tasks_for_event(
    *,
    event_search_text: str,
    tasks: list[Task],
    limit: int = AUTO_ROUTE_MAX_CANDIDATES,
) -> list[tuple[Task, float]]:
    """Return tasks sorted by descending confidence score."""
    event_tokens = tokenize_search_text(event_search_text)
    scored = [(task, score_task_for_event(event_tokens=event_tokens, task=task)) for task in tasks]
    scored.sort(key=lambda row: (-row[1], row[0].id))
    return scored[:limit]


def pick_auto_route(
    ranked: list[tuple[Task, float]],
    *,
    min_confidence: float = AUTO_ROUTE_MIN_CONFIDENCE,
    min_margin: float = AUTO_ROUTE_MIN_MARGIN,
) -> Task | None:
    """Return the task to auto-route, or ``None`` when routing should stall."""
    if not ranked:
        return None
    top_task, top_score = ranked[0]
    if top_score < min_confidence:
        return None
    if len(ranked) == 1:
        return top_task
    second_score = ranked[1][1]
    if top_score - second_score < min_margin:
        return None
    return top_task


def candidates_above_threshold(
    ranked: list[tuple[Task, float]],
    *,
    min_confidence: float = AUTO_ROUTE_MIN_CONFIDENCE,
) -> list[tuple[Task, float]]:
    """Return ranked tasks whose confidence meets the auto-route floor."""
    return [(task, score) for task, score in ranked if score >= min_confidence]
