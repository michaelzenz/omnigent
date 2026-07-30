"""Global scheduler for host timer items."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.timer.context import TimerContext, build_timer_http_client
from omnigent.host.timer.handlers import DEFAULT_TIMER_HANDLERS
from omnigent.host.timer.protocol import TimerHandler

_logger = logging.getLogger(__name__)

_DEFAULT_TICK_S = 30.0
_SLEEP = asyncio.sleep


class TimerScheduler:
    """Tick, scan due timer items for this host, and run handlers serially."""

    def __init__(
        self,
        *,
        server_url: str,
        host_id: str,
        handlers: dict[str, TimerHandler] | None = None,
        tick_s: float = _DEFAULT_TICK_S,
        sleep: Callable[[float], Awaitable[None]] = _SLEEP,
    ) -> None:
        self._server_url = server_url
        self._host_id = host_id
        self._handlers = handlers if handlers is not None else dict(DEFAULT_TIMER_HANDLERS)
        self._tick_s = tick_s
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None
        self._ctx: TimerContext | None = None
        self._task: asyncio.Task[None] | None = None
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        """Create the shared HTTP client and start the tick loop."""
        if self._started:
            return
        self._client = build_timer_http_client(self._server_url, host_id=self._host_id)
        self._ctx = TimerContext(
            server_url=self._server_url,
            host_id=self._host_id,
            client=self._client,
            config_path=CONFIG_PATH,
        )
        self._started = True
        self._task = asyncio.create_task(self._tick_loop(), name="host-timer")
        _logger.info("TimerScheduler started (tick=%ss)", self._tick_s)

    async def stop(self) -> None:
        """Stop the tick loop and close the shared HTTP client."""
        if not self._started:
            return
        self._started = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._ctx = None
        _logger.info("TimerScheduler stopped")

    async def run_until_stopped(self) -> None:
        """Start the scheduler and wait until :meth:`stop` is called."""
        await self.start()
        try:
            while self._started:
                await self._sleep(3600.0)
        except asyncio.CancelledError:
            await self.stop()
            raise

    async def tick_once(self) -> None:
        """Scan due items once and execute them serially."""
        assert self._ctx is not None
        response = await self._ctx.client.get("/v1/timer-items/due")
        response.raise_for_status()
        payload = response.json()
        items = payload.get("data", [])
        if not isinstance(items, list):
            return
        for raw in items:
            if not isinstance(raw, dict):
                continue
            await self._process_item(raw)

    async def _tick_loop(self) -> None:
        while self._started:
            started = time.perf_counter()
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.warning("TimerScheduler tick failed", exc_info=True)
            elapsed = time.perf_counter() - started
            delay = max(self._tick_s - elapsed, 0.0)
            if not self._started:
                break
            await self._sleep(delay)

    async def _process_item(self, raw: dict[str, Any]) -> None:
        assert self._ctx is not None
        item_id = raw.get("id")
        task_type = raw.get("task_type")
        payload = raw.get("payload")
        if not isinstance(item_id, str) or not item_id:
            return
        if not isinstance(task_type, str) or not task_type:
            return
        if not isinstance(payload, dict):
            payload = {}

        claim = await self._ctx.client.post(f"/v1/timer-items/{item_id}/claim")
        if claim.status_code == 404:
            return
        claim.raise_for_status()

        handler = self._handlers.get(task_type)
        if handler is None:
            _logger.warning("TimerScheduler: no handler for task_type=%s", task_type)
            await self._ctx.client.post(f"/v1/timer-items/{item_id}/fail")
            return

        try:
            await handler.handle(self._ctx, item_id=item_id, payload=payload)
        except Exception:
            _logger.warning(
                "TimerScheduler: handler %s failed for item %s",
                task_type,
                item_id,
                exc_info=True,
            )
            await self._ctx.client.post(f"/v1/timer-items/{item_id}/fail")
            return

        complete = await self._ctx.client.post(f"/v1/timer-items/{item_id}/complete")
        if complete.status_code == 404:
            _logger.warning("TimerScheduler: complete rejected for item %s", item_id)
            return
        complete.raise_for_status()
