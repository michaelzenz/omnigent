"""Ambient bridge from standalone Codex sessions into Omnigent.

Watches ``~/.codex`` rollout JSONL files created by the real Codex app/CLI
and mirrors them into Omnigent as imported codex-native sessions. Initial
history is imported via ``POST /v1/imports``; subsequent turns are appended
through ``external_conversation_item`` events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import yaml

from omnigent.chat import _remote_headers
from omnigent.entities import NewConversationItem
from omnigent.host.identity import CONFIG_PATH
from omnigent.session_import.codex_rollout import (
    active_codex_rollout_path,
    default_codex_home,
    iter_codex_rollout_paths,
    read_codex_rollout_from_offset,
    thread_id_from_rollout_path,
)
from omnigent.session_import.local import load_codex_session
from omnigent.session_import.models import SessionImportNotFoundError, import_conversation_id

_logger = logging.getLogger(__name__)

_ENV_VAR = "OMNIGENT_CODEX_AMBIENT_SYNC"
_CONFIG_KEY = "codex_ambient_sync"
_DEFAULT_POLL_INTERVAL_S = 3.0
_POST_TIMEOUT_S = 30.0
_STATE_FILE = Path.home() / ".omnigent" / "codex_ambient_bridge.json"
# Rollouts older than this before bridge startup are not auto-imported.
_STARTUP_DISCOVERY_SKEW_MS = 24 * 60 * 60 * 1000


@dataclass
class _TrackedRollout:
    """One mirrored Codex rollout and its Omnigent session cursor."""

    thread_id: str
    rollout_path: str
    session_id: str
    byte_offset: int
    turn_id: str = "history"
    workspace: str | None = None


@dataclass
class _BridgeState:
    """Persisted rollout cursors keyed by Codex thread id."""

    threads: dict[str, _TrackedRollout]
    started_at_ms: int | None = None

    @classmethod
    def load(cls, path: Path = _STATE_FILE) -> _BridgeState:
        """Load persisted bridge state, or an empty state when absent."""
        if not path.exists():
            return cls(threads={}, started_at_ms=None)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(threads={}, started_at_ms=None)
        threads_raw = raw.get("threads") if isinstance(raw, dict) else None
        started_at_ms = raw.get("started_at_ms") if isinstance(raw, dict) else None
        if not isinstance(threads_raw, dict):
            return cls(
                threads={},
                started_at_ms=started_at_ms if isinstance(started_at_ms, int) else None,
            )
        threads: dict[str, _TrackedRollout] = {}
        for thread_id, entry in threads_raw.items():
            if not isinstance(thread_id, str) or not isinstance(entry, dict):
                continue
            session_id = entry.get("session_id")
            rollout_path = entry.get("rollout_path")
            byte_offset = entry.get("byte_offset")
            if not isinstance(session_id, str) or not isinstance(rollout_path, str):
                continue
            if not isinstance(byte_offset, int) or byte_offset < 0:
                continue
            threads[thread_id] = _TrackedRollout(
                thread_id=thread_id,
                rollout_path=rollout_path,
                session_id=session_id,
                byte_offset=byte_offset,
                turn_id=entry.get("turn_id")
                if isinstance(entry.get("turn_id"), str)
                else "history",
                workspace=entry.get("workspace")
                if isinstance(entry.get("workspace"), str)
                else None,
            )
        return cls(
            threads=threads,
            started_at_ms=started_at_ms if isinstance(started_at_ms, int) else None,
        )

    def save(self, path: Path = _STATE_FILE) -> None:
        """Persist rollout cursors atomically."""
        payload = {
            "started_at_ms": self.started_at_ms,
            "threads": {
                thread_id: asdict(tracked) for thread_id, tracked in self.threads.items()
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)


def codex_ambient_sync_enabled(config_path: Path = CONFIG_PATH) -> bool:
    """Return whether the host daemon should mirror standalone Codex sessions."""
    env_value = os.environ.get(_ENV_VAR)
    if env_value is not None:
        return env_value.strip().lower() not in {"0", "false", "no", "off"}
    if not config_path.exists():
        return True
    try:
        with config_path.open(encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
    except OSError:
        return True
    host_section = cfg.get("host") if isinstance(cfg, dict) else None
    if not isinstance(host_section, dict):
        return True
    configured = host_section.get(_CONFIG_KEY)
    if configured is None:
        return True
    if isinstance(configured, bool):
        return configured
    if isinstance(configured, str):
        return configured.strip().lower() not in {"0", "false", "no", "off"}
    return True


def _build_http_headers(server_url: str) -> dict[str, str]:
    """Build Omnigent HTTP headers for the ambient bridge."""
    headers = dict(_remote_headers(server_url=server_url))
    if "Authorization" in headers:
        return headers
    try:
        from omnigent.runner._entry import _make_auth_token_factory

        factory = _make_auth_token_factory(server_url=server_url)
        token = factory() if factory else None
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:  # noqa: BLE001
        _logger.debug("Could not obtain auth token for ambient bridge", exc_info=True)
    return headers


def _serialize_import_items(items: tuple[NewConversationItem, ...]) -> list[dict[str, object]]:
    """Serialize normalized items for ``POST /v1/imports``."""
    return [
        {
            "type": item.type,
            "response_id": item.response_id,
            "data": item.data.model_dump(mode="json", exclude_none=True),
        }
        for item in items
    ]


async def _import_codex_session(
    client: httpx.AsyncClient,
    *,
    thread_id: str,
    workspace: str | None,
    items: tuple[NewConversationItem, ...],
) -> str:
    """Import one Codex thread, returning the Omnigent session id."""
    session_id = import_conversation_id("codex", thread_id)
    response = await client.post(
        "/v1/imports",
        json={
            "source": "codex",
            "external_session_id": thread_id,
            "workspace": workspace,
            "items": _serialize_import_items(items),
        },
    )
    if response.status_code == 409:
        return session_id
    response.raise_for_status()
    payload = response.json()
    imported_id = payload.get("session_id") if isinstance(payload, dict) else None
    return imported_id if isinstance(imported_id, str) and imported_id else session_id


async def _post_external_conversation_item(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    item: NewConversationItem,
) -> None:
    """Mirror one Codex rollout item into an imported Omnigent session."""
    response = await client.post(
        f"/v1/sessions/{session_id}/events",
        json={
            "type": "external_conversation_item",
            "data": {
                "item_type": item.type,
                "item_data": item.data.model_dump(mode="json", exclude_none=True),
                "response_id": item.response_id,
            },
        },
    )
    response.raise_for_status()


async def _delete_omnigent_session(client: httpx.AsyncClient, *, session_id: str) -> None:
    """Delete one mirrored Omnigent session after Codex removes it."""
    response = await client.delete(f"/v1/sessions/{session_id}")
    if response.status_code == 404:
        return
    response.raise_for_status()


async def _prune_deleted_codex_sessions(
    client: httpx.AsyncClient,
    *,
    state: _BridgeState,
    codex_home: Path,
) -> _BridgeState:
    """Drop tracked threads whose Codex rollout no longer exists in sessions/."""
    if not state.threads:
        return state
    remaining = dict(state.threads)
    changed = False
    for thread_id, tracked in list(state.threads.items()):
        if active_codex_rollout_path(codex_home, thread_id) is not None:
            continue
        try:
            await _delete_omnigent_session(client, session_id=tracked.session_id)
        except httpx.HTTPError:
            _logger.warning(
                "Failed to delete Omnigent session %s for removed Codex thread %s",
                tracked.session_id,
                thread_id,
                exc_info=True,
            )
            continue
        remaining.pop(thread_id, None)
        changed = True
        _logger.info(
            "Deleted Omnigent session %s after Codex removed thread %s",
            tracked.session_id,
            thread_id,
        )
    if not changed:
        return state
    return _BridgeState(threads=remaining, started_at_ms=state.started_at_ms)


async def _ensure_tracked_rollout(
    client: httpx.AsyncClient,
    *,
    state: _BridgeState,
    rollout_path: Path,
    codex_home: Path,
    started_at_ms: int,
) -> _TrackedRollout | None:
    """Import a newly discovered rollout when it has readable history."""
    thread_id = thread_id_from_rollout_path(rollout_path)
    if thread_id is None:
        return None
    existing = state.threads.get(thread_id)
    if existing is not None:
        if existing.rollout_path != str(rollout_path):
            existing = _TrackedRollout(
                thread_id=existing.thread_id,
                rollout_path=str(rollout_path),
                session_id=existing.session_id,
                byte_offset=existing.byte_offset,
                turn_id=existing.turn_id,
                workspace=existing.workspace,
            )
            state.threads[thread_id] = existing
        return existing

    rollout_mtime_ms = int(rollout_path.stat().st_mtime * 1000)
    if rollout_mtime_ms < started_at_ms - _STARTUP_DISCOVERY_SKEW_MS:
        return None

    try:
        imported = load_codex_session(thread_id, codex_home=codex_home)
    except SessionImportNotFoundError:
        return None

    session_id = await _import_codex_session(
        client,
        thread_id=thread_id,
        workspace=imported.workspace,
        items=imported.items,
    )
    tracked = _TrackedRollout(
        thread_id=thread_id,
        rollout_path=str(rollout_path),
        session_id=session_id,
        byte_offset=rollout_path.stat().st_size,
        turn_id="history",
        workspace=imported.workspace,
    )
    state.threads[thread_id] = tracked
    _logger.info(
        "Imported standalone Codex session %s as Omnigent session %s",
        thread_id,
        session_id,
    )
    return tracked


async def _sync_tracked_rollout(
    client: httpx.AsyncClient,
    *,
    tracked: _TrackedRollout,
) -> _TrackedRollout:
    """Tail one rollout and mirror any newly appended items."""
    rollout_path = Path(tracked.rollout_path)
    if not rollout_path.is_file():
        return tracked

    read_result = read_codex_rollout_from_offset(
        rollout_path,
        byte_offset=tracked.byte_offset,
        turn_id=tracked.turn_id,
        workspace=tracked.workspace,
    )
    if not read_result.items and read_result.byte_offset == tracked.byte_offset:
        return tracked

    for item in read_result.items:
        await _post_external_conversation_item(client, session_id=tracked.session_id, item=item)

    return _TrackedRollout(
        thread_id=tracked.thread_id,
        rollout_path=tracked.rollout_path,
        session_id=tracked.session_id,
        byte_offset=read_result.byte_offset,
        turn_id=read_result.turn_id,
        workspace=read_result.workspace or tracked.workspace,
    )


async def _poll_codex_ambient_once(
    client: httpx.AsyncClient,
    *,
    state: _BridgeState,
    codex_home: Path,
    state_path: Path = _STATE_FILE,
) -> _BridgeState:
    """Scan Codex rollouts once and mirror any new history."""
    changed = False
    pruned = await _prune_deleted_codex_sessions(client, state=state, codex_home=codex_home)
    if pruned is not state:
        state = pruned
        changed = True
    if state.started_at_ms is None:
        state = _BridgeState(threads=state.threads, started_at_ms=int(time.time() * 1000))
        changed = True
    started_at_ms = state.started_at_ms or int(time.time() * 1000)
    for rollout_path in iter_codex_rollout_paths(codex_home):
        tracked = await _ensure_tracked_rollout(
            client,
            state=state,
            rollout_path=rollout_path,
            codex_home=codex_home,
            started_at_ms=started_at_ms,
        )
        if tracked is None:
            continue
        previous = state.threads.get(tracked.thread_id)
        synced = await _sync_tracked_rollout(client, tracked=tracked)
        if previous != synced:
            state.threads[tracked.thread_id] = synced
            changed = True
    if changed:
        state.save(state_path)
    return state


async def run_codex_ambient_bridge(
    server_url: str,
    *,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    codex_home: Path | None = None,
    state_path: Path = _STATE_FILE,
) -> None:
    """Poll standalone Codex rollouts and mirror them into Omnigent."""
    home = codex_home or default_codex_home()
    state = _BridgeState.load(state_path)
    headers = _build_http_headers(server_url)
    timeout = httpx.Timeout(_POST_TIMEOUT_S)
    async with httpx.AsyncClient(
        base_url=server_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
    ) as client:
        while True:
            try:
                state = await _poll_codex_ambient_once(
                    client,
                    state=state,
                    codex_home=home,
                    state_path=state_path,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — ambient sync must survive transient failures
                _logger.warning("Codex ambient bridge poll failed", exc_info=True)
            await asyncio.sleep(poll_interval_s)


__all__ = [
    "codex_ambient_sync_enabled",
    "run_codex_ambient_bridge",
]
