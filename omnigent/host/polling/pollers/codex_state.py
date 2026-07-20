"""Shared Codex ambient bridge state and server sync helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from omnigent.ambient_codex import AMBIENT_IMPORT_MAX_AGE_MS
from omnigent.entities import NewConversationItem
from omnigent.session_import.models import import_conversation_id
from omnigent.ssh_connections_store import SshConnectionProfile, read_ssh_connections


@dataclass
class TrackedRollout:
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
class BridgeState:
    """In-memory rollout cursors keyed by tracked state id."""

    threads: dict[str, TrackedRollout]


@dataclass
class BridgeStateDelta:
    """Thread updates produced by one sub-poller tick."""

    updated: dict[str, TrackedRollout]
    removed: set[str]


def apply_bridge_delta(state: BridgeState, delta: BridgeStateDelta) -> BridgeState:
    """Merge one sub-poller delta into shared bridge state."""
    if not delta.updated and not delta.removed:
        return state
    threads = dict(state.threads)
    for key in delta.removed:
        threads.pop(key, None)
    threads.update(delta.updated)
    return BridgeState(threads=threads)


def merge_bridge_deltas(*deltas: BridgeStateDelta) -> BridgeStateDelta:
    """Combine multiple sub-poller deltas into one merge step."""
    updated: dict[str, TrackedRollout] = {}
    removed: set[str] = set()
    for delta in deltas:
        removed.difference_update(delta.updated)
        removed.update(delta.removed)
        for key, tracked in delta.updated.items():
            if key not in removed:
                updated[key] = tracked
    for key in removed:
        updated.pop(key, None)
    return BridgeStateDelta(updated=updated, removed=removed)


def tracked_state_key(tracked: TrackedRollout) -> str:
    if tracked.ssh_alias:
        return f"{tracked.ssh_alias}:{tracked.thread_id}"
    return tracked.thread_id


def import_external_session_id(thread_id: str, ssh_alias: str | None) -> str:
    if ssh_alias:
        return f"{ssh_alias}:{thread_id}"
    return thread_id


def profile_for_connection_id(connection_id: str | None) -> SshConnectionProfile | None:
    if connection_id is None:
        return None
    for profile in read_ssh_connections():
        if profile.id == connection_id:
            return profile
    return None


def ssh_alias_for_connection_id(connection_id: str | None) -> str | None:
    profile = profile_for_connection_id(connection_id)
    return profile.alias if profile is not None else None


def rollout_is_recent(mtime_ms: int) -> bool:
    now_ms = int(time.time() * 1000)
    return mtime_ms >= now_ms - AMBIENT_IMPORT_MAX_AGE_MS


def serialize_import_items(items: tuple[NewConversationItem, ...]) -> list[dict[str, object]]:
    """Serialize normalized items for import and ambient sync requests."""
    return [
        {
            "type": item.type,
            "response_id": item.response_id,
            "data": item.data.model_dump(mode="json", exclude_none=True),
        }
        for item in items
    ]


async def hydrate_bridge_state(client: httpx.AsyncClient, *, host_id: str) -> BridgeState:
    """Load server-owned ambient tracks for this host."""
    response = await client.get(f"/v1/hosts/{host_id}/ambient/codex")
    response.raise_for_status()
    payload = response.json()
    tracks = payload.get("tracks") if isinstance(payload, dict) else None
    if not isinstance(tracks, list):
        return BridgeState(threads={})
    threads: dict[str, TrackedRollout] = {}
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
        tracked = TrackedRollout(
            thread_id=thread_id,
            rollout_path=rollout_path,
            session_id=session_id,
            byte_offset=byte_offset,
            turn_id=turn_id if isinstance(turn_id, str) else "history",
            workspace=workspace if isinstance(workspace, str) else None,
            connection_id=connection_id,
            ssh_alias=ssh_alias_for_connection_id(connection_id),
        )
        threads[tracked_state_key(tracked)] = tracked
    return BridgeState(threads=threads)


async def import_codex_session(
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
    external_session_id = import_external_session_id(thread_id, ssh_alias)
    response = await client.post(
        "/v1/imports",
        json={
            "source": "codex",
            "external_session_id": external_session_id,
            "workspace": workspace,
            "items": serialize_import_items(items),
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


async def post_ambient_codex_sync(
    client: httpx.AsyncClient,
    *,
    tracked: TrackedRollout,
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
                "items": serialize_import_items(items),
                "byte_offset": byte_offset,
                "turn_id": turn_id,
                "rollout_path": tracked.rollout_path,
                "connection_id": tracked.connection_id,
            },
        },
    )
    response.raise_for_status()


async def delete_omnigent_session(client: httpx.AsyncClient, *, session_id: str) -> None:
    """Delete one mirrored Omnigent session after Codex removes it."""
    response = await client.delete(f"/v1/sessions/{session_id}")
    if response.status_code == 404:
        return
    response.raise_for_status()
