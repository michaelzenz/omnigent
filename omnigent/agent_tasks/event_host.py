"""Host attribution for task events — host-aware broker clustering.

Events from different hosts must never be clustered together: a batch goes to
one manager, and a manager on host A cannot act on sessions living on host B.
"""

from __future__ import annotations

from omnigent.entities import EventTag, TaskEvent

HOST_TAG_TYPE = "host"
_SOURCE_OFFSET_PREFIX = "host:"


def host_tag(host_id: str | None) -> EventTag | None:
    """Build the host EventTag for an event emitted on ``host_id``."""
    if not host_id:
        return None
    return EventTag(tag_type=HOST_TAG_TYPE, tag=host_id)


def event_host(event: TaskEvent) -> str | None:
    """Best-effort host attribution for one event.

    Reads the explicit ``host`` tag first, then the ``host:<id>``
    ``source_offset`` convention used by the external session watcher.
    """
    for tag in event.tags or []:
        if tag.tag_type == HOST_TAG_TYPE:
            return tag.tag
    if event.source_offset and event.source_offset.startswith(_SOURCE_OFFSET_PREFIX):
        return event.source_offset[len(_SOURCE_OFFSET_PREFIX) :]
    return None
