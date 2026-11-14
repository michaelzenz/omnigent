"""Queue the task events waiting for each user's broker, and write the notices that carry them."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from typing import Any

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.event_batches import broker_batches, cluster_by_tags, is_session_event
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_role_session
from omnigent.agent_tasks.roles import BROKER_ROLE_KEY, builtin_role, session_role
from omnigent.entities import AgentQueueItem, TaskEvent
from omnigent.server.agent_task_delivery import session_mid_turn

_logger = logging.getLogger(__name__)

# How many notices may carry an event the broker did not route.
BROKER_MAX_DELIVERIES = 3
_BROKER_PROMPT = "[System: route these events to managers]\n"


class BrokerPackager:
    """Queues the events waiting for each user's broker, in v2's batches."""

    def __init__(self, *, board: BoardStores, sessions: RoleSessionStores) -> None:
        """
        :param board: Holds the events and the queue.
        :param sessions: Where each user's broker session lives.
        """
        role = builtin_role(BROKER_ROLE_KEY)
        assert role is not None
        self._role = role
        self._board = board
        self._sessions = sessions

    async def package(self, now: int) -> None:
        """Queue the batches due at ``now``, user by user."""
        waiting = await asyncio.to_thread(
            self._board.events.list_waiting,
            state="unrouted",
            max_deliveries=BROKER_MAX_DELIVERIES,
        )
        by_owner: defaultdict[str | None, list[TaskEvent]] = defaultdict(list)
        for event in waiting:
            by_owner[event.owner_user_id].append(event)
        for owner, events in by_owner.items():
            try:
                await self._package_for(owner, events, now)
            except Exception:
                _logger.exception("GlobalHub broker packaging failed for %s", owner)

    async def _package_for(self, owner: str | None, events: list[TaskEvent], now: int) -> None:
        settings = self._sessions.user_settings_store
        if await asyncio.to_thread(settings.get_globalhub_placement, user_id=owner) is None:
            # Without a GlobalHub host there is no broker yet; the events wait.
            return
        broker, _ = await ensure_role_session(self._role, user_id=owner, stores=self._sessions)
        for batch in broker_batches(events, now=now, broker_idle=not session_mid_turn(broker)):
            await asyncio.to_thread(
                self._board.queue.enqueue,
                uuid.uuid4().hex,
                owner_user_id=owner,
                target_session_id=broker.id,
                kind="events",
                event_ids=[event.id for event in batch],
            )


class EventNoticeWriter:
    """Writes the notice text for ``events`` queue items when they are sent."""

    def __init__(self, *, board: BoardStores, conversations: Any) -> None:
        """
        :param board: Holds the events the items carry.
        :param conversations: Finds the target and source sessions.
        """
        self._board = board
        self._conversations = conversations

    def write(self, item: AgentQueueItem) -> str | None:
        """
        Return the notice text, or ``None`` when none of the item's events still waits. Blocking.

        Leaves out events that passed to another role since they were queued,
        and dismisses events about sessions deleted before delivery.
        """
        target = self._conversations.get_conversation(item.target_session_id)
        role = session_role(target.labels) if target is not None else None
        if role is None or role.key != BROKER_ROLE_KEY:
            raise ValueError("an events notice goes only to a broker session")
        owner = item.owner_user_id
        waiting: list[TaskEvent] = []
        for event in self._board.events.list(
            owner_user_id=owner, states=["unrouted"], queue_item_ids=[item.id]
        ):
            if self._about_deleted_session(event):
                self._board.events.dismiss(event.id, owner_user_id=owner)
            else:
                waiting.append(event)
        if not waiting:
            return None
        clusters = [
            {"tags": _tags(cluster[0]), "events": [_notice_entry(event) for event in cluster]}
            for cluster in cluster_by_tags(waiting)
        ]
        return json.dumps(
            {"prompt": _BROKER_PROMPT, "clusters": clusters}, ensure_ascii=False, indent=2
        )

    def _about_deleted_session(self, event: TaskEvent) -> bool:
        return (
            is_session_event(event)
            and event.source_session_id is not None
            and self._conversations.get_conversation(event.source_session_id) is None
        )


def _tags(event: TaskEvent) -> list[dict[str, str]]:
    return [{"tag_type": tag.tag_type, "tag": tag.tag} for tag in event.tags]


def _notice_entry(event: TaskEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "title": event.title,
        "state": event.state,
        "source": event.source,
        "source_key": event.source_key,
        "created_at": event.created_at,
        "tags": _tags(event),
        "payload": event.payload,
    }
