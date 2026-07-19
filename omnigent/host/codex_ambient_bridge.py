"""Ambient bridge from standalone Codex sessions into Omnigent.

Watches ``~/.codex`` rollout JSONL files created by the real Codex app/CLI
and mirrors them into Omnigent as imported codex-native sessions. Initial
history is imported via ``POST /v1/imports``; subsequent turns are appended
through ``ambient_codex_sync`` events. Poll cursors live on the server.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from omnigent.ambient_codex import AMBIENT_IMPORT_MAX_AGE_MS, HOST_AMBIENT_ID_HEADER
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
from omnigent.session_import.local import load_codex_session, load_codex_session_from_rollout
from omnigent.session_import.models import SessionImportNotFoundError, import_conversation_id
from omnigent.ssh_connections_store import SshConnectionProfile, read_ssh_connections
from omnigent.ssh_remote import (
    ssh_remote_active_codex_rollout,
    ssh_remote_codex_rollouts,
    ssh_remote_file_size,
    ssh_remote_rollout_to_tempfile,
    ssh_run,
)

_logger = logging.getLogger(__name__)

_ENV_VAR = "OMNIGENT_CODEX_AMBIENT_SYNC"
_CONFIG_KEY = "codex_ambient_sync"
_DEFAULT_POLL_INTERVAL_S = 3.0
_POST_TIMEOUT_S = 30.0


@dataclass
class _TrackedRollout:
    """One mirrored Codex rollout and its in-memory Omnigent cursor."""

    thread_id: str
    rollout_path: str
    session_id: str
    byte_offset: int
    turn_id: str = "history"
    workspace: str | None = None
    connection_id: str | None = None
    ssh_alias: str | None = None


@dataclass
class _BridgeState:
    """In-memory rollout cursors keyed by tracked state id."""

    threads: dict[str, _TrackedRollout]


def _tracked_state_key(tracked: _TrackedRollout) -> str:
    if tracked.ssh_alias:
        return f"{tracked.ssh_alias}:{tracked.thread_id}"
    return tracked.thread_id


def _import_external_session_id(thread_id: str, ssh_alias: str | None) -> str:
    if ssh_alias:
        return f"{ssh_alias}:{thread_id}"
    return thread_id


def _ssh_alias_for_connection_id(connection_id: str | None) -> str | None:
    profile = _profile_for_connection_id(connection_id)
    return profile.alias if profile is not None else None


def _profile_for_connection_id(connection_id: str | None) -> SshConnectionProfile | None:
    if connection_id is None:
        return None
    for profile in read_ssh_connections():
        if profile.id == connection_id:
            return profile
    return None


def _rollout_is_recent(mtime_ms: int) -> bool:
    now_ms = int(time.time() * 1000)
    return mtime_ms >= now_ms - AMBIENT_IMPORT_MAX_AGE_MS


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


def _build_http_headers(server_url: str, *, host_id: str) -> dict[str, str]:
    """Build Omnigent HTTP headers for the ambient bridge."""
    headers = dict(_remote_headers(server_url=server_url))
    headers[HOST_AMBIENT_ID_HEADER] = host_id
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
    """Serialize normalized items for import and ambient sync requests."""
    return [
        {
            "type": item.type,
            "response_id": item.response_id,
            "data": item.data.model_dump(mode="json", exclude_none=True),
        }
        for item in items
    ]


async def _hydrate_bridge_state(client: httpx.AsyncClient, *, host_id: str) -> _BridgeState:
    """Load server-owned ambient tracks for this host."""
    response = await client.get(f"/v1/hosts/{host_id}/ambient/codex")
    response.raise_for_status()
    payload = response.json()
    tracks = payload.get("tracks") if isinstance(payload, dict) else None
    if not isinstance(tracks, list):
        return _BridgeState(threads={})
    threads: dict[str, _TrackedRollout] = {}
    for entry in tracks:
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("session_id")
        thread_id = entry.get("thread_id")
        rollout_path = entry.get("rollout_path")
        byte_offset = entry.get("byte_offset")
        if (
            not isinstance(session_id, str)
            or not isinstance(thread_id, str)
            or not isinstance(rollout_path, str)
            or not isinstance(byte_offset, int)
        ):
            continue
        connection_id = entry.get("connection_id")
        if connection_id is not None and not isinstance(connection_id, str):
            connection_id = None
        turn_id = entry.get("turn_id")
        workspace = entry.get("workspace")
        tracked = _TrackedRollout(
            thread_id=thread_id,
            rollout_path=rollout_path,
            session_id=session_id,
            byte_offset=byte_offset,
            turn_id=turn_id if isinstance(turn_id, str) else "history",
            workspace=workspace if isinstance(workspace, str) else None,
            connection_id=connection_id,
            ssh_alias=_ssh_alias_for_connection_id(connection_id),
        )
        threads[_tracked_state_key(tracked)] = tracked
    return _BridgeState(threads=threads)


async def _import_codex_session(
    client: httpx.AsyncClient,
    *,
    thread_id: str,
    workspace: str | None,
    items: tuple[NewConversationItem, ...],
    rollout_path: str,
    byte_offset: int,
    connection_id: str | None,
    ssh_alias: str | None = None,
) -> str | None:
    """Import one Codex thread, returning the Omnigent session id when claimed."""
    external_session_id = _import_external_session_id(thread_id, ssh_alias)
    response = await client.post(
        "/v1/imports",
        json={
            "source": "codex",
            "external_session_id": external_session_id,
            "workspace": workspace,
            "items": _serialize_import_items(items),
            "ambient_codex": {
                "byte_offset": byte_offset,
                "turn_id": "history",
                "rollout_path": rollout_path,
                "connection_id": connection_id,
            },
        },
    )
    if response.status_code == 409:
        return None
    response.raise_for_status()
    payload = response.json()
    imported_id = payload.get("session_id") if isinstance(payload, dict) else None
    fallback = import_conversation_id("codex", external_session_id)
    return imported_id if isinstance(imported_id, str) and imported_id else fallback


async def _post_ambient_codex_sync(
    client: httpx.AsyncClient,
    *,
    tracked: _TrackedRollout,
    items: tuple[NewConversationItem, ...],
    byte_offset: int,
    turn_id: str,
) -> None:
    """Mirror one Codex rollout batch and advance the server cursor."""
    response = await client.post(
        f"/v1/sessions/{tracked.session_id}/events",
        json={
            "type": "ambient_codex_sync",
            "data": {
                "items": _serialize_import_items(items),
                "byte_offset": byte_offset,
                "turn_id": turn_id,
                "rollout_path": tracked.rollout_path,
                "connection_id": tracked.connection_id,
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
    """Drop tracked local threads whose Codex rollout no longer exists."""
    if not state.threads:
        return state
    remaining = dict(state.threads)
    changed = False
    for state_key, tracked in list(state.threads.items()):
        if tracked.ssh_alias is not None:
            continue
        if active_codex_rollout_path(codex_home, tracked.thread_id) is not None:
            continue
        rollout_path = Path(tracked.rollout_path)
        if rollout_path.is_file():
            continue
        try:
            await _delete_omnigent_session(client, session_id=tracked.session_id)
        except httpx.HTTPError:
            _logger.warning(
                "Failed to delete Omnigent session %s for removed Codex thread %s",
                tracked.session_id,
                tracked.thread_id,
                exc_info=True,
            )
            continue
        remaining.pop(state_key, None)
        changed = True
        _logger.info(
            "Deleted Omnigent session %s after Codex removed thread %s",
            tracked.session_id,
            tracked.thread_id,
        )
    if not changed:
        return state
    return _BridgeState(threads=remaining)


async def _ensure_tracked_rollout(
    client: httpx.AsyncClient,
    *,
    state: _BridgeState,
    rollout_path: Path,
    codex_home: Path,
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
                connection_id=existing.connection_id,
                ssh_alias=existing.ssh_alias,
            )
            state.threads[thread_id] = existing
        return existing

    rollout_mtime_ms = int(rollout_path.stat().st_mtime * 1000)
    if not _rollout_is_recent(rollout_mtime_ms):
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
        rollout_path=str(rollout_path),
        byte_offset=rollout_path.stat().st_size,
        connection_id=None,
    )
    if session_id is None:
        return None
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
    if tracked.ssh_alias is None and not rollout_path.is_file():
        return tracked

    read_result = read_codex_rollout_from_offset(
        rollout_path,
        byte_offset=tracked.byte_offset,
        turn_id=tracked.turn_id,
        workspace=tracked.workspace,
    )
    if not read_result.items and read_result.byte_offset == tracked.byte_offset:
        return tracked

    await _post_ambient_codex_sync(
        client,
        tracked=tracked,
        items=read_result.items,
        byte_offset=read_result.byte_offset,
        turn_id=read_result.turn_id,
    )

    return _TrackedRollout(
        thread_id=tracked.thread_id,
        rollout_path=tracked.rollout_path,
        session_id=tracked.session_id,
        byte_offset=read_result.byte_offset,
        turn_id=read_result.turn_id,
        workspace=read_result.workspace or tracked.workspace,
        connection_id=tracked.connection_id,
        ssh_alias=tracked.ssh_alias,
    )


async def _remote_rollout_exists(alias: str, remote_path: str) -> bool:
    """Return whether a rollout file still exists on the remote host."""
    quoted = shlex.quote(remote_path)
    code, _, _ = await ssh_run(alias, f"test -f {quoted}")
    return code == 0


async def _prune_deleted_remote_codex_sessions(
    client: httpx.AsyncClient,
    *,
    state: _BridgeState,
    profile: SshConnectionProfile,
) -> _BridgeState:
    """Drop remote tracked threads whose active rollout disappeared."""
    if not state.threads:
        return state
    remaining = dict(state.threads)
    changed = False
    for state_key, tracked in list(state.threads.items()):
        if tracked.ssh_alias != profile.alias:
            continue
        active = await ssh_remote_active_codex_rollout(profile.alias, tracked.thread_id)
        if active is not None:
            continue
        if await _remote_rollout_exists(profile.alias, tracked.rollout_path):
            continue
        try:
            await _delete_omnigent_session(client, session_id=tracked.session_id)
        except httpx.HTTPError:
            _logger.warning(
                "Failed to delete Omnigent session %s for removed remote Codex thread %s",
                tracked.session_id,
                tracked.thread_id,
                exc_info=True,
            )
            continue
        remaining.pop(state_key, None)
        changed = True
    if not changed:
        return state
    return _BridgeState(threads=remaining)


async def _ensure_remote_tracked_rollout(
    client: httpx.AsyncClient,
    *,
    state: _BridgeState,
    profile: SshConnectionProfile,
    remote_path: str,
    rollout_mtime_ms: int,
) -> _TrackedRollout | None:
    """Import a newly discovered remote rollout when it has readable history."""
    thread_id = thread_id_from_rollout_path(Path(remote_path))
    if thread_id is None:
        return None
    state_key = f"{profile.alias}:{thread_id}"
    existing = state.threads.get(state_key)
    if existing is not None:
        if existing.rollout_path != remote_path:
            existing = _TrackedRollout(
                thread_id=existing.thread_id,
                rollout_path=remote_path,
                session_id=existing.session_id,
                byte_offset=existing.byte_offset,
                turn_id=existing.turn_id,
                workspace=existing.workspace,
                connection_id=profile.id,
                ssh_alias=profile.alias,
            )
            state.threads[state_key] = existing
        return existing

    if not _rollout_is_recent(rollout_mtime_ms):
        return None

    temp_path: Path | None = None
    try:
        temp_path = await ssh_remote_rollout_to_tempfile(profile.alias, remote_path)
        imported = load_codex_session_from_rollout(temp_path, thread_id)
        remote_size = await ssh_remote_file_size(profile.alias, remote_path)
    except (OSError, SessionImportNotFoundError):
        return None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    session_id = await _import_codex_session(
        client,
        thread_id=thread_id,
        workspace=imported.workspace,
        items=imported.items,
        rollout_path=remote_path,
        byte_offset=remote_size,
        connection_id=profile.id,
        ssh_alias=profile.alias,
    )
    if session_id is None:
        return None
    tracked = _TrackedRollout(
        thread_id=thread_id,
        rollout_path=remote_path,
        session_id=session_id,
        byte_offset=remote_size,
        turn_id="history",
        workspace=imported.workspace,
        connection_id=profile.id,
        ssh_alias=profile.alias,
    )
    state.threads[state_key] = tracked
    _logger.info(
        "Imported remote Codex session %s via %s as Omnigent session %s",
        thread_id,
        profile.alias,
        session_id,
    )
    return tracked


async def _sync_remote_tracked_rollout(
    client: httpx.AsyncClient,
    *,
    tracked: _TrackedRollout,
) -> _TrackedRollout:
    """Tail one remote rollout and mirror any newly appended items."""
    if tracked.ssh_alias is None:
        return tracked
    temp_path: Path | None = None
    try:
        temp_path = await ssh_remote_rollout_to_tempfile(
            tracked.ssh_alias,
            tracked.rollout_path,
            byte_offset=tracked.byte_offset,
        )
        read_result = read_codex_rollout_from_offset(
            temp_path,
            byte_offset=0,
            turn_id=tracked.turn_id,
            workspace=tracked.workspace,
        )
    except OSError:
        return tracked
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    if not read_result.items and read_result.byte_offset == 0:
        return tracked

    new_offset = tracked.byte_offset + read_result.byte_offset
    await _post_ambient_codex_sync(
        client,
        tracked=tracked,
        items=read_result.items,
        byte_offset=new_offset,
        turn_id=read_result.turn_id,
    )

    return _TrackedRollout(
        thread_id=tracked.thread_id,
        rollout_path=tracked.rollout_path,
        session_id=tracked.session_id,
        byte_offset=new_offset,
        turn_id=read_result.turn_id,
        workspace=read_result.workspace or tracked.workspace,
        connection_id=tracked.connection_id,
        ssh_alias=tracked.ssh_alias,
    )


async def _poll_remote_codex_once(
    client: httpx.AsyncClient,
    *,
    state: _BridgeState,
    profile: SshConnectionProfile,
) -> tuple[_BridgeState, bool]:
    """Scan one SSH alias for remote Codex rollouts and mirror new history."""
    changed = False
    pruned = await _prune_deleted_remote_codex_sessions(client, state=state, profile=profile)
    if pruned is not state:
        state = pruned
        changed = True
    try:
        rollouts = await ssh_remote_codex_rollouts(profile.alias)
    except OSError:
        _logger.warning("Failed to list remote Codex rollouts via %s", profile.alias, exc_info=True)
        return state, changed
    for rollout in rollouts:
        tracked = await _ensure_remote_tracked_rollout(
            client,
            state=state,
            profile=profile,
            remote_path=rollout.path,
            rollout_mtime_ms=rollout.mtime_ms,
        )
        if tracked is None:
            continue
        previous = state.threads.get(_tracked_state_key(tracked))
        synced = await _sync_remote_tracked_rollout(client, tracked=tracked)
        if previous != synced:
            state.threads[_tracked_state_key(synced)] = synced
            changed = True
    return state, changed


async def _poll_codex_ambient_once(
    client: httpx.AsyncClient,
    *,
    state: _BridgeState,
    codex_home: Path,
) -> _BridgeState:
    """Scan Codex rollouts once and mirror any new history."""
    pruned = await _prune_deleted_codex_sessions(client, state=state, codex_home=codex_home)
    if pruned is not state:
        state = pruned
    for rollout_path in iter_codex_rollout_paths(codex_home):
        tracked = await _ensure_tracked_rollout(
            client,
            state=state,
            rollout_path=rollout_path,
            codex_home=codex_home,
        )
        if tracked is None:
            continue
        previous = state.threads.get(_tracked_state_key(tracked))
        synced = await _sync_tracked_rollout(client, tracked=tracked)
        if previous != synced:
            state.threads[_tracked_state_key(synced)] = synced
    for profile in read_ssh_connections():
        if not profile.codex_remote:
            continue
        state, _remote_changed = await _poll_remote_codex_once(
            client,
            state=state,
            profile=profile,
        )
    return state


async def run_codex_ambient_bridge(
    server_url: str,
    *,
    host_id: str,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    codex_home: Path | None = None,
) -> None:
    """Poll standalone Codex rollouts and mirror them into Omnigent."""
    home = codex_home or default_codex_home()
    headers = _build_http_headers(server_url, host_id=host_id)
    timeout = httpx.Timeout(_POST_TIMEOUT_S)
    async with httpx.AsyncClient(
        base_url=server_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
    ) as client:
        state = await _hydrate_bridge_state(client, host_id=host_id)
        while True:
            try:
                state = await _poll_codex_ambient_once(
                    client,
                    state=state,
                    codex_home=home,
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
