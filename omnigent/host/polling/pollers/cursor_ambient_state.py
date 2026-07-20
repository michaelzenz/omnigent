"""Backward-compatible re-exports for Cursor ambient state."""

from __future__ import annotations

from typing import Literal

from omnigent.host.polling.pollers.ambient_state import (
    AmbientBridgeState,
    AmbientBridgeStateDelta,
    TrackedAmbientSession,
    apply_bridge_delta,
    delete_omnigent_session,
    hydrate_ambient_bridge_state,
    import_ambient_session,
    import_external_session_id,
    merge_bridge_deltas,
    post_ambient_sync,
    profile_for_connection_id,
    serialize_import_items,
    source_is_recent,
    ssh_alias_for_connection_id,
    tracked_state_key,
)
from omnigent.session_import.cursor_common import session_key_from_external_session_id

CursorAmbientSource = Literal["cursor-cli", "cursor-ide"]
CursorBridgeState = AmbientBridgeState
CursorBridgeStateDelta = AmbientBridgeStateDelta
TrackedCursorSession = TrackedAmbientSession
apply_cursor_bridge_delta = apply_bridge_delta
merge_cursor_bridge_deltas = merge_bridge_deltas


def TrackedCursorSession_from(
    session_key: str,
    source_path: str,
    session_id: str,
    byte_offset: int,
    *,
    turn_id: str = "history",
    workspace: str | None = None,
    connection_id: str | None = None,
    ssh_alias: str | None = None,
    import_source: CursorAmbientSource = "cursor-cli",
) -> TrackedAmbientSession:
    return TrackedAmbientSession(
        session_key=session_key,
        source_path=source_path,
        session_id=session_id,
        byte_offset=byte_offset,
        turn_id=turn_id,
        workspace=workspace,
        connection_id=connection_id,
        ssh_alias=ssh_alias,
        import_source=import_source,
    )


async def hydrate_cursor_bridge_state(
    client,
    *,
    host_id: str,
    import_source: CursorAmbientSource,
) -> AmbientBridgeState:
    return await hydrate_ambient_bridge_state(
        client,
        host_id=host_id,
        import_source=import_source,
    )


async def import_cursor_session(
    client,
    *,
    import_source,
    session_key: str,
    workspace: str | None,
    items,
    source_path: str,
    byte_offset: int,
    connection_id: str | None,
    ssh_alias: str | None = None,
) -> str | None:
    return await import_ambient_session(
        client,
        import_source=import_source,
        session_key=session_key,
        workspace=workspace,
        items=items,
        source_path=source_path,
        byte_offset=byte_offset,
        connection_id=connection_id,
        ssh_alias=ssh_alias,
    )


async def post_ambient_cursor_sync(
    client,
    *,
    tracked: TrackedAmbientSession,
    items,
    byte_offset: int,
    turn_id: str,
) -> None:
    await post_ambient_sync(
        client,
        tracked=tracked,
        items=items,
        byte_offset=byte_offset,
        turn_id=turn_id,
    )


def session_key_from_track_external_id(external_session_id: str) -> str | None:
    return session_key_from_external_session_id(external_session_id)
