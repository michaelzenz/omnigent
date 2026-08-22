"""Initialization for internal PuppyGarden Workers."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from omnigent.entities import Worker
from omnigent.stores.worker_store import WorkerStore


async def initialize_internal_worker(
    worker: Worker,
    *,
    worker_store: WorkerStore,
    session_creator: Any,
    app_state: Any,
    user_id: str | None = None,
) -> Worker:
    """Create the target session for a Worker already marked initializing."""
    try:
        from omnigent.server.routes.sessions import _make_internal_request
        from omnigent.server.schemas import SessionCreateRequest

        snapshot = json.loads(worker.provider_configuration or "{}")
        if snapshot.get("kind") != "internal":
            raise ValueError("External Worker adapters are not installed")
        configuration = snapshot.get("configuration") or {}
        launch = snapshot.get("launch") or {}
        agent_id = configuration.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("The Worker Provider has no harness")
        response = await asyncio.wait_for(
            session_creator(
                body=SessionCreateRequest(
                    agent_id=agent_id,
                    title=worker.provider_name or "PuppyGarden worker",
                    host_id=launch.get("host_id"),
                    workspace=launch.get("workspace"),
                    model_override=configuration.get("model"),
                ),
                request=_make_internal_request(app_state),
                user_id=user_id,
            ),
            timeout=120.0,
        )
        current = await asyncio.to_thread(worker_store.get_worker, worker.id)
        if current is not None and current.state == "initializing":
            updated = await asyncio.to_thread(
                worker_store.update_worker,
                worker.id,
                target_id=response.id,
                state="idle",
                failure_reason=None,
            )
            assert updated is not None
            return updated
    except Exception as exc:  # noqa: BLE001
        current = await asyncio.to_thread(worker_store.get_worker, worker.id)
        if current is not None and current.state == "initializing":
            updated = await asyncio.to_thread(
                worker_store.update_worker,
                worker.id,
                state="initialization_failed",
                failure_reason=str(exc),
            )
            assert updated is not None
            return updated
    current = await asyncio.to_thread(worker_store.get_worker, worker.id)
    if current is None:
        raise RuntimeError("Worker disappeared during initialization")
    return current
