"""Search-text helpers for agent-task routing."""

from __future__ import annotations

from omnigent.entities import TaskEventTag, TaskTag


def build_task_search_text(
    *,
    title: str,
    charter: str | None,
    tags: list[TaskTag],
) -> str:
    """
    Build the plain searchable mirror for a task.

    :param title: Task title.
    :param charter: Keyword-dense routing charter, or ``None``.
    :param tags: Typed tags attached to the task.
    :returns: Newline-joined searchable text.
    """
    parts = [title.strip()]
    if charter:
        parts.append(charter.strip())
    for tag in sorted(tags, key=lambda row: (row.tag_type, row.tag)):
        parts.append(f"{tag.tag_type}:{tag.tag}")
    return "\n".join(part for part in parts if part)


def build_event_search_text(
    *,
    event_type: str,
    title: str,
    summary: str | None,
    tags: list[TaskEventTag],
) -> str:
    """
    Build the plain searchable mirror for a task event.

    :param event_type: Machine-readable event classifier.
    :param title: Human-readable one-liner.
    :param summary: Optional extraction summary.
    :param tags: Typed tags attached to the event.
    :returns: Newline-joined searchable text.
    """
    parts = [event_type.strip(), title.strip()]
    if summary:
        parts.append(summary.strip())
    for tag in sorted(tags, key=lambda row: (row.tag_type, row.tag)):
        parts.append(f"{tag.tag_type}:{tag.tag}")
    return "\n".join(part for part in parts if part)
