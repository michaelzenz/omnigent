"""Global scheduler for host ambient source pollers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.context import PollContext, build_poll_http_client
from omnigent.host.polling.protocol import PollSource

_logger = logging.getLogger(__name__)

_DEFAULT_BACKOFF_CAP_S = 30.0
_SLEEP = asyncio.sleep


@dataclass
class PollSourceStats:
    """Runtime counters for one registered poller."""

    last_ok_at: float | None = None
    last_err_at: float | None = None
    last_duration_s: float | None = None
    skipped_overlaps: int = 0
    consecutive_failures: int = 0
    backoff_s: float = 0.0


class PollScheduler:
    """Runs one asyncio task per enabled :class:`PollSource`."""

    def __init__(
        self,
        *,
        server_url: str,
        host_id: str,
        config_path: Path = CONFIG_PATH,
        sleep: Callable[[float], Awaitable[None]] = _SLEEP,
        backoff_cap_s: float = _DEFAULT_BACKOFF_CAP_S,
    ) -> None:
        self._server_url = server_url
        self._host_id = host_id
        self._config_path = config_path
        self._sleep = sleep
        self._backoff_cap_s = backoff_cap_s
        self._pollers: dict[str, PollSource] = {}
        self._stats: dict[str, PollSourceStats] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running: dict[str, bool] = {}
        self._client: httpx.AsyncClient | None = None
        self._ctx: PollContext | None = None
        self._started = False

    def register(self, poller: PollSource) -> None:
        """Register a poller before :meth:`start`."""
        self._pollers[poller.name] = poller
        self._stats.setdefault(poller.name, PollSourceStats())

    def stats(self) -> dict[str, PollSourceStats]:
        """Return a snapshot of per-poller runtime counters."""
        return dict(self._stats)

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        """Create the shared HTTP client and start enabled poller tasks."""
        if self._started:
            return
        self._client = build_poll_http_client(self._server_url, host_id=self._host_id)
        self._ctx = PollContext(
            server_url=self._server_url,
            host_id=self._host_id,
            client=self._client,
            config_path=self._config_path,
        )
        self._started = True
        for name, poller in self._pollers.items():
            if not poller.enabled(self._ctx):
                continue
            self._tasks[name] = asyncio.create_task(
                self._poll_loop(poller),
                name=f"host-poll-{name}",
            )
        _logger.info("PollScheduler started with %d poller(s)", len(self._tasks))

    async def stop(self) -> None:
        """Stop poller tasks and close the shared HTTP client."""
        if not self._started:
            return
        self._started = False
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._running.clear()
        for poller in self._pollers.values():
            with contextlib.suppress(Exception):
                await poller.on_stop()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._ctx = None
        _logger.info("PollScheduler stopped")

    async def run_until_stopped(self) -> None:
        """Start the scheduler and wait until :meth:`stop` is called."""
        await self.start()
        try:
            while self._started:
                await self._sleep(3600.0)
        except asyncio.CancelledError:
            await self.stop()
            raise

    async def _poll_loop(self, poller: PollSource) -> None:
        assert self._ctx is not None
        stats = self._stats[poller.name]
        try:
            await poller.on_start(self._ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.warning("Poller %s on_start failed", poller.name, exc_info=True)

        in_flight: asyncio.Task[None] | None = None
        while self._started:
            if in_flight is not None and not in_flight.done():
                stats.skipped_overlaps += 1
            elif in_flight is not None:
                await self._finish_poll_task(in_flight, poller, stats)
                in_flight = None

            if in_flight is None:
                in_flight = asyncio.create_task(
                    self._execute_poll(poller, stats),
                    name=f"host-poll-{poller.name}-tick",
                )

            if not self._started:
                break
            delay = max(poller.interval_s(self._ctx), stats.backoff_s)
            await self._sleep(delay)

        if in_flight is not None:
            in_flight.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await in_flight

    async def _execute_poll(self, poller: PollSource, stats: PollSourceStats) -> None:
        assert self._ctx is not None
        started = time.perf_counter()
        self._running[poller.name] = True
        try:
            await poller.poll_once(self._ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            stats.consecutive_failures += 1
            base = poller.interval_s(self._ctx)
            stats.backoff_s = min(
                self._backoff_cap_s,
                base * (2 ** min(stats.consecutive_failures, 5)),
            )
            stats.last_err_at = time.time()
            _logger.warning("Poller %s poll_once failed", poller.name, exc_info=True)
        else:
            stats.consecutive_failures = 0
            stats.backoff_s = 0.0
            stats.last_ok_at = time.time()
        finally:
            self._running[poller.name] = False
            stats.last_duration_s = time.perf_counter() - started

    async def _finish_poll_task(
        self,
        task: asyncio.Task[None],
        poller: PollSource,
        stats: PollSourceStats,
    ) -> None:
        try:
            await task
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.debug("Poller %s tick failed after completion", poller.name, exc_info=True)
