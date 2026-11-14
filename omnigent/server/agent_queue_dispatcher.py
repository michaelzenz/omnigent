"""Deliver queued GlobalHub messages to role sessions, one turn at a time per session.

Every few seconds the packagers queue items from waiting board work. Each pass
claims every session's oldest queued item, checks that its target still serves
its role, writes the message text and sends it. A sent item holds its session
until the turn handling it ends: main's status hook reports the turn end, and
a session idle for a minute after the send counts too. A turn whose runner died
in a crash ends the next time the dispatcher reads the session, which settles
the turn the way main's session list does.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.role_sessions import RoleSessionStores, is_role_session_current
from omnigent.entities import AgentQueueItem
from omnigent.server.agent_task_delivery import (
    SendResult,
    SessionSendDeps,
    send_to_session,
    session_mid_turn,
    settle_orphaned_turns,
)
from omnigent.server.auth import RESERVED_USER_LOCAL

_logger = logging.getLogger(__name__)

SCAN_INTERVAL_S = 1.0
PACKAGE_INTERVAL_S = 5
# A sent item whose session stays idle this long counts as handled, in case its
# turn end was never reported, e.g. when the server restarted mid-turn.
IDLE_FALLBACK_S = 60
# Longer than any runner wake, so only a claim left by a crashed send is this old.
STALE_CLAIM_S = 10 * 60
BUSY_RECHECK_S = 5
_STALE_SWEEP_INTERVAL_S = 60
_BASE_RETRY_DELAY_S = 30
_MAX_RETRY_DELAY_S = 5 * 60

# Writes an item's message text, or returns None when nothing is left to say.
ItemRenderer = Callable[[AgentQueueItem], str | None]
Sender = Callable[..., Awaitable[SendResult]]
# Settles the given sessions whose turn lost its runner and returns their ids.
OrphanSettler = Callable[..., Awaitable[list[str]]]


class Packager(Protocol):
    """Queues the items that waiting board work makes due."""

    async def package(self, now: int) -> None:
        """Queue what is due at ``now``, in Unix epoch seconds."""


def retry_delay_s(attempts: int) -> int:
    """Return the wait after a failed send, given how many sends failed before it."""
    return min(_BASE_RETRY_DELAY_S * 2**attempts, _MAX_RETRY_DELAY_S)


class AgentQueueDispatcher:
    """Sends each GlobalHub role session its queued messages, one turn at a time."""

    def __init__(
        self,
        *,
        board: BoardStores,
        sessions: RoleSessionStores,
        send_deps: SessionSendDeps,
        renderers: Mapping[str, ItemRenderer],
        packagers: Sequence[Packager] = (),
        send: Sender = send_to_session,
        settle_orphans: OrphanSettler = settle_orphaned_turns,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """
        :param board: Holds the queue and the managers.
        :param sessions: Where role sessions and their records live.
        :param send_deps: How a send reaches a session's runner.
        :param renderers: Writes the message text for each item kind.
        :param packagers: Queue items from waiting board work, every
            :data:`PACKAGE_INTERVAL_S` seconds.
        :param send: Sends one message into a session.
        :param settle_orphans: Settles the sent items' sessions whose turn lost
            its runner.
        :param clock: Returns Unix epoch seconds.
        """
        self._queue = board.queue
        self._managers = board.managers
        self._sessions = sessions
        self._send_deps = send_deps
        self._renderers = dict(renderers)
        self._packagers = tuple(packagers)
        self._send = send
        self._settle_orphans = settle_orphans
        self._clock = clock
        self._last_package: int | None = None
        self._loop: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        # Sessions with a sent item, refreshed from the queue on every pass.
        self._in_flight: set[str] = set()
        # When each sent item's session was first seen idle after the send.
        self._idle_since: dict[str, int] = {}
        self._last_stale_sweep: int | None = None

    async def start(self) -> None:
        """Put back the claims a stopped server left, then start the delivery loop."""
        if self._loop is None:
            # GlobalHub runs on one server, so a claim found at start is from a stopped process.
            try:
                await asyncio.to_thread(
                    self._queue.release_stale_claims, claimed_before=int(self._clock()) + 1
                )
            except Exception:
                _logger.exception("GlobalHub could not put back the claims a stopped server left")
            self._loop = asyncio.create_task(self._run_forever(), name="globalhub-agent-queue")

    async def stop(self) -> None:
        """Stop the loop and the sends in progress; their claims go back to the queue later."""
        tasks = [*self._tasks, *([self._loop] if self._loop is not None else [])]
        self._loop = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def turn_ended(self, session_id: str) -> None:
        """Free a session whose turn ended for its next item; a no-op outside an event loop."""
        if session_id not in self._in_flight:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._in_flight.discard(session_id)
        self._track(
            loop.create_task(asyncio.to_thread(self._queue.mark_done_for_session, session_id))
        )

    async def run_once(self) -> list[asyncio.Task[Any]]:
        """Run one pass and return the deliveries it started."""
        now = int(self._clock())
        if (
            self._last_stale_sweep is None
            or now - self._last_stale_sweep >= _STALE_SWEEP_INTERVAL_S
        ):
            self._last_stale_sweep = now
            await asyncio.to_thread(
                self._queue.release_stale_claims, claimed_before=now - STALE_CLAIM_S
            )
        if self._last_package is None or now - self._last_package >= PACKAGE_INTERVAL_S:
            self._last_package = now
            for packager in self._packagers:
                try:
                    await packager.package(now)
                except Exception:
                    _logger.exception("GlobalHub packager %r failed", packager)
        await self._settle_sent(now)
        claimed = await asyncio.to_thread(self._queue.claim_due, now=now)
        return [self._track(asyncio.create_task(self._deliver(item))) for item in claimed]

    async def _run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                _logger.exception("GlobalHub queue pass failed")
            await asyncio.sleep(SCAN_INTERVAL_S)

    async def _settle_sent(self, now: int) -> None:
        """Mark done the sent items whose turn lost its runner, or whose session stayed idle."""
        sent = await asyncio.to_thread(self._queue.list_sent)
        self._in_flight = {item.target_session_id for item in sent}
        if not sent:
            self._idle_since = {}
            return
        targets = await asyncio.to_thread(
            self._send_deps.conversation_store.get_conversations,
            [item.target_session_id for item in sent],
        )
        orphaned = set(await self._settle_orphans(targets.values(), deps=self._send_deps))
        idle_since: dict[str, int] = {}
        for item in sent:
            if item.target_session_id in orphaned:
                _logger.info(
                    "GlobalHub settled session %s, whose turn lost its runner",
                    item.target_session_id,
                )
            else:
                conv = targets.get(item.target_session_id)
                if conv is not None and session_mid_turn(conv):
                    continue
                since = self._idle_since.get(item.id, now)
                if now - since < IDLE_FALLBACK_S:
                    idle_since[item.id] = since
                    continue
            await asyncio.to_thread(
                self._queue.mark_done, item.id, owner_user_id=item.owner_user_id
            )
            self._in_flight.discard(item.target_session_id)
        self._idle_since = idle_since

    async def _deliver(self, item: AgentQueueItem) -> None:
        try:
            result = await self._attempt(item)
        except Exception as exc:
            _logger.warning("GlobalHub queue item %s was not delivered", item.id, exc_info=True)
            result = SendResult("failed", str(exc) or type(exc).__name__)
        await asyncio.to_thread(self._settle, item, result)
        if result is not None and result.outcome == "sent":
            self._in_flight.add(item.target_session_id)

    async def _attempt(self, item: AgentQueueItem) -> SendResult | None:
        """Send the item; ``None`` when it has nothing left to say."""
        if not await asyncio.to_thread(self._target_is_current, item):
            return SendResult("gone")
        render = self._renderers.get(item.kind)
        if render is None:
            return SendResult("failed", f"no message text is written for {item.kind!r} items")
        text = await asyncio.to_thread(render, item)
        if text is None:
            return None
        return await self._send(
            item.target_session_id,
            text,
            created_by=item.owner_user_id or RESERVED_USER_LOCAL,
            deps=self._send_deps,
        )

    def _target_is_current(self, item: AgentQueueItem) -> bool:
        conv = self._send_deps.conversation_store.get_conversation(item.target_session_id)
        return conv is not None and is_role_session_current(
            conv, user_id=item.owner_user_id, stores=self._sessions, managers=self._managers
        )

    def _settle(self, item: AgentQueueItem, result: SendResult | None) -> None:
        owner = item.owner_user_id
        now = int(self._clock())
        if result is None or result.outcome == "gone":
            # Nothing left to say, or no session to say it to: release its events.
            self._queue.delete(item.id, owner_user_id=owner)
        elif result.outcome == "sent":
            self._queue.mark_sent(item.id, owner_user_id=owner)
        elif result.outcome == "busy":
            self._queue.defer(item.id, owner_user_id=owner, not_before=now + BUSY_RECHECK_S)
        else:
            self._queue.retry_later(
                item.id,
                owner_user_id=owner,
                error=result.error or result.outcome,
                not_before=now + retry_delay_s(item.attempts),
            )

    def _track(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        self._tasks.add(task)
        task.add_done_callback(self._forget)
        return task

    def _forget(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _logger.error("GlobalHub queue task failed", exc_info=task.exception())
