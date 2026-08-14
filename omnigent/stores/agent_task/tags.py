"""Tag helpers for agent-task routing."""

from __future__ import annotations

import json
from typing import Any

from omnigent.entities import EventTag, TaskTag

_TAG_FIELDS = frozenset({"tag_type", "tag"})


def normalize_tag_type(tag_type: str) -> str:
    return tag_type.strip()


def normalize_tag_value(tag: str) -> str:
    return tag.strip()


def tag_pair(tag_type: str, tag: str) -> tuple[str, str]:
    return (normalize_tag_type(tag_type), normalize_tag_value(tag))


def sort_tags(tags: list[EventTag]) -> list[EventTag]:
    return sorted(tags, key=lambda row: (row.tag_type, row.tag))


def encode_event_tags(tags: list[EventTag]) -> str:
    """Serialize immutable ingress tags to a JSON array."""
    payload = [
        {
            "tag_type": normalize_tag_type(tag.tag_type),
            "tag": normalize_tag_value(tag.tag),
        }
        for tag in sort_tags(tags)
    ]
    return json.dumps(payload, separators=(",", ":"))


def decode_event_tags(raw: str | None) -> list[EventTag]:
    """Deserialize tags stored on a task event row."""
    if raw is None or not raw.strip():
        return []
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("task event tags must be a JSON array")
    tags: list[EventTag] = []
    seen: set[tuple[str, str]] = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            raise ValueError("task event tag entries must be JSON objects")
        if not _TAG_FIELDS.issubset(entry):
            raise ValueError("task event tag entries require tag_type and tag")
        key = tag_pair(str(entry["tag_type"]), str(entry["tag"]))
        if key in seen:
            continue
        seen.add(key)
        tags.append(EventTag(tag_type=key[0], tag=key[1]))
    return sort_tags(tags)


def tags_to_payload(tags: list[EventTag]) -> list[dict[str, str]]:
    return [{"tag_type": tag.tag_type, "tag": tag.tag} for tag in sort_tags(tags)]


def tag_fingerprint(tags: list[EventTag]) -> tuple[tuple[str, str], ...]:
    return tuple(tag_pair(tag.tag_type, tag.tag) for tag in sort_tags(tags))


def merge_event_tags(events: list[Any]) -> list[EventTag]:
    """Merge tags from multiple events; last event wins per tag_type."""
    tag_map: dict[str, str] = {}
    for event in events:
        for tag in event.tags:
            tag_map[normalize_tag_type(tag.tag_type)] = normalize_tag_value(tag.tag)
    return [EventTag(tag_type=tag_type, tag=tag) for tag_type, tag in sorted(tag_map.items())]


def task_tag_pairs(task_tags: list[TaskTag]) -> set[tuple[str, str]]:
    return {tag_pair(tag.tag_type, tag.tag) for tag in task_tags}
