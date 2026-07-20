"""Shared ambient bridge state and server sync helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Literal

import httpx

from omnigent.ambient_codex import AMBIENT_IMPORT_MAX_AGE_MS
from omnigent.entities import NewConversationItem
from omnigent.session_import.models import ImportSource, import_conversation_id
from omnigent.ssh_connections_store import SshConnectionProfile, read_ssh_connections

AmbientImportSource = Literal["codex", "cursor-projects"]

_HYDRATE_ENDPOINT: dict[AmbientImportSource, str] = {
    "codex": "codex",
    "cursor-projects": "cursor-projects",
}
_SYNC_EVENT_TYPE: dict[AmbientImportSource, str] = {
    "codex": "ambient_codex_sync",
    "cursor-projects": "ambient_cursor_projects_sync",
}
_IMPORT_AMBIENT_KEY: dict[AmbientImportSource, str] = {
    "codex": "ambient_codex",
    "cursor-projects": "ambient_track",
}
_IMPORT_PATH_KEY: dict[AmbientImportSource, str] = {
    "codex": "rollout_path",
    "cursor-projects": "source_path",
}
_SYNC_PATH_KEY: dict[AmbientImportSource, str] = {
    "codex": "rollout_path",
    "cursor-projects": "source_path",
}
_HYDRATE_SESSION_KEY_FIELD: dict[AmbientImportSource, str] = {
    "codex": "thread_id",
    "cursor-projects": "session_key",
}
_HYDRATE_PATH_FIELD: dict[AmbientImportSource, str] = {
    "codex": "rollout_path",
    "cursor-projects": "source_path",
}


@dataclass
class TrackedAmbientSession:
    """One mirrored external session and its in-memory Omnigent cursor."""

    session_key: str
    source_path: str
    session_id: str
    byte_offset: int
    turn_id: str = "history"
    workspace: str | None = None
    connection_id: str | None = None
    ssh_alias: str | None = None
    import_source: AmbientImportSource = "codex"

    @property
    def thread_id(self) -> str:
        return self.session_key

    @property
    def rollout_path(self) -> str:
        return self.source_path


class AmbientBridgeState:
    """In-memory ambient session cursors keyed by tracked state id."""

    def __init__(
        self,
        tracks: dict[str, TrackedAmbientSession] | None = None,
        *,
        threads: dict[str, TrackedAmbientSession] | None = None,
        sessions: dict[str, TrackedAmbientSession] | None = None,
    ) -> None:
        if tracks is not None:
            self.tracks = tracks
        elif threads is not None:
            self.tracks = threads
        elif sessions is not None:
            self.tracks = sessions
        else:
            self.tracks = {}

    @property
    def threads(self) -> dict[str, TrackedAmbientSession]:
        return self.tracks

    @property
    def sessions(self) -> dict[str, TrackedAmbientSession]:
        return self.tracks


@dataclass
class AmbientBridgeStateDelta:
    """Track updates produced by one sub-poller tick."""

    updated: dict[str, TrackedAmbientSession]
    removed: set[str]


def apply_bridge_delta(
    state: AmbientBridgeState,
    delta: AmbientBridgeStateDelta,
) -> AmbientBridgeState:
    """Merge one sub-poller delta into shared bridge state."""
    if not delta.updated and not delta.removed:
        return state
    tracks = dict(state.tracks)
    for key in delta.removed:
        tracks.pop(key, None)
    tracks.update(delta.updated)
    return AmbientBridgeState(tracks=tracks)


def merge_bridge_deltas(*deltas: AmbientBridgeStateDelta) -> AmbientBridgeStateDelta:
    """Combine multiple sub-poller deltas into one merge step."""
    updated: dict[str, TrackedAmbientSession] = {}
    removed: set[str] = set()
    for delta in deltas:
        removed.difference_update(delta.updated)
        removed.update(delta.removed)
        for key, tracked in delta.updated.items():
            if key not in removed:
                updated[key] = tracked
    for key in removed:
        updated.pop(key, None)
    return AmbientBridgeStateDelta(updated=updated, removed=removed)


def tracked_state_key(tracked: TrackedAmbientSession) -> str:
    if tracked.ssh_alias:
        return f"{tracked.ssh_alias}:{tracked.session_key}"
    return tracked.session_key


def import_external_session_id(session_key: str, ssh_alias: str | None) -> str:
    if ssh_alias:
        return f"{ssh_alias}:{session_key}"
    return session_key


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


def source_is_recent(mtime_ms: int) -> bool:
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


async def hydrate_ambient_bridge_state(
    client: httpx.AsyncClient,
    *,
    host_id: str,
    import_source: AmbientImportSource,
) -> AmbientBridgeState:
    """Load server-owned ambient tracks for this host."""
    endpoint = _HYDRATE_ENDPOINT[import_source]
    session_key_field = _HYDRATE_SESSION_KEY_FIELD[import_source]
    path_field = _HYDRATE_PATH_FIELD[import_source]
    response = await client.get(f"/v1/hosts/{host_id}/ambient/{endpoint}")
    response.raise_for_status()
    payload = response.json()
    tracks_payload = payload.get("tracks") if isinstance(payload, dict) else None
    if not isinstance(tracks_payload, list):
        return AmbientBridgeState(tracks={})
    tracks: dict[str, TrackedAmbientSession] = {}
    for entry in tracks_payload:
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("session_id")
        session_key = entry.get(session_key_field)
        source_path = entry.get(path_field)
        byte_offset = entry.get("byte_offset")
        if (
            not isinstance(session_id, str)
            or not isinstance(session_key, str)
            or not isinstance(source_path, str)
            or not isinstance(byte_offset, int)
        ):
            continue
        connection_id = entry.get("connection_id")
        if connection_id is not None and not isinstance(connection_id, str):
            connection_id = None
        turn_id = entry.get("turn_id")
        workspace = entry.get("workspace")
        tracked = TrackedAmbientSession(
            session_key=session_key,
            source_path=source_path,
            session_id=session_id,
            byte_offset=byte_offset,
            turn_id=turn_id if isinstance(turn_id, str) else "history",
            workspace=workspace if isinstance(workspace, str) else None,
            connection_id=connection_id,
            ssh_alias=ssh_alias_for_connection_id(connection_id),
            import_source=import_source,
        )
        tracks[tracked_state_key(tracked)] = tracked
    return AmbientBridgeState(tracks=tracks)


async def import_ambient_session(
    client: httpx.AsyncClient,
    *,
    import_source: ImportSource,
    session_key: str,
    workspace: str | None,
    items: tuple[NewConversationItem, ...],
    source_path: str,
    byte_offset: int,
    connection_id: str | None,
    ssh_alias: str | None = None,
) -> str | None:
    """Import one external session, returning the Omnigent session id when claimed."""
    external_session_id = import_external_session_id(session_key, ssh_alias)
    ambient_key = _IMPORT_AMBIENT_KEY[import_source]
    path_key = _IMPORT_PATH_KEY[import_source]
    response = await client.post(
        "/v1/imports",
        json={
            "source": import_source,
            "external_session_id": external_session_id,
            "workspace": workspace,
            "items": serialize_import_items(items),
            ambient_key: {
                "byte_offset": byte_offset,
                "turn_id": "history",
                path_key: source_path,
                "connection_id": connection_id,
            },
        },
    )
    if response.status_code == 409:
        return None
    response.raise_for_status()
    payload = response.json()
    imported_id = payload.get("session_id") if isinstance(payload, dict) else None
    fallback = import_conversation_id(import_source, external_session_id)
    return imported_id if isinstance(imported_id, str) and imported_id else fallback


async def post_ambient_sync(
    client: httpx.AsyncClient,
    *,
    tracked: TrackedAmbientSession,
    items: tuple[NewConversationItem, ...],
    byte_offset: int,
    turn_id: str,
) -> None:
    """Mirror one ambient batch and advance the server cursor."""
    path_key = _SYNC_PATH_KEY[tracked.import_source]
    response = await client.post(
        f"/v1/sessions/{tracked.session_id}/events",
        json={
            "type": _SYNC_EVENT_TYPE[tracked.import_source],
            "data": {
                "items": serialize_import_items(items),
                "byte_offset": byte_offset,
                "turn_id": turn_id,
                path_key: tracked.source_path,
                "connection_id": tracked.connection_id,
            },
        },
    )
    response.raise_for_status()


async def delete_omnigent_session(client: httpx.AsyncClient, *, session_id: str) -> None:
    """Delete one mirrored Omnigent session after the external source removes it."""
    response = await client.delete(f"/v1/sessions/{session_id}")
    if response.status_code == 404:
        return
    response.raise_for_status()


def replace_tracked(
    tracked: TrackedAmbientSession,
    **changes: object,
) -> TrackedAmbientSession:
    """Return a copy of *tracked* with selected fields replaced."""
    return replace(tracked, **changes)
