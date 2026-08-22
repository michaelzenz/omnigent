"""Target-independent contract implemented by PuppyGarden Worker adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from omnigent.entities import Worker
from omnigent.entities.worker_provider import WorkerProvider


@dataclass(frozen=True)
class WorkerCapabilities:
    initialize: bool = True
    multi_turn: bool = True
    streaming: bool = True
    interrupt: bool = True
    terminate: bool = True
    observe_response_request: bool = True
    resume: bool = True


@dataclass(frozen=True)
class WorkerObservation:
    activity: Literal["idle", "busy"]
    connected: bool
    needs_response: bool = False
    response_notice: str | None = None
    output_delta: str | None = None
    final_result: str | None = None
    failure_reason: str | None = None


class WorkerAdapter(Protocol):
    """Minimum protocol for an internal or external Worker target."""

    capabilities: WorkerCapabilities

    async def available(self, provider: WorkerProvider) -> tuple[bool, str | None]: ...

    async def initialize(self, worker: Worker, configuration: dict[str, Any]) -> str:
        """Initialize asynchronously and return the target system's durable id."""

    async def send(self, worker: Worker, dispatch_id: str, message: str) -> None:
        """Idempotently start one turn for Agent Queue's dispatch id."""

    async def interrupt(self, worker: Worker, dispatch_id: str) -> None: ...

    async def terminate(self, worker: Worker) -> None: ...

    async def observe(self, worker: Worker) -> WorkerObservation: ...

    async def rebind(self, worker: Worker) -> None: ...
