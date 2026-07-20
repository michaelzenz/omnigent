"""Local filesystem Cursor IDE transcript polling."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.polling.pollers.ambient_state import TrackedAmbientSession
from omnigent.host.polling.pollers.ambient_subpoller import AmbientReadResult, LocalAmbientSubPollerBase
from omnigent.session_import.cursor_ide import (
    default_cursor_projects_root,
    initial_cursor_ide_byte_offset,
    iter_cursor_ide_transcripts,
    load_cursor_ide_session,
    read_cursor_ide_from_offset,
)
from omnigent.session_import.models import LocalSessionImport


class CursorIdeLocalSubPoller(LocalAmbientSubPollerBase):
    """Scan ``~/.cursor/projects`` IDE transcripts on the host machine."""

    def __init__(self, *, projects_root: Path | None = None) -> None:
        super().__init__()
        self._projects_root = projects_root or default_cursor_projects_root()

    def _source_label(self) -> str:
        return "Cursor IDE"

    def _import_source(self):
        return "cursor-ide"

    def _iter_discoveries(self):
        return iter_cursor_ide_transcripts(self._projects_root)

    def _discovery_session_key(self, discovery) -> str:
        return discovery.transcript_id

    def _discovery_source_path(self, discovery) -> str:
        return str(discovery.transcript_path)

    def _discovery_mtime_ms(self, discovery) -> int:
        return discovery.mtime_ms

    async def _local_source_missing(self, tracked: TrackedAmbientSession) -> bool:
        return not Path(tracked.source_path).is_file()

    def _load_import(self, discovery) -> LocalSessionImport:
        return load_cursor_ide_session(discovery.transcript_path, workspace=discovery.workspace)

    def _initial_byte_offset(self, discovery, imported: LocalSessionImport) -> int:
        return initial_cursor_ide_byte_offset(discovery.transcript_path)

    def _read_updates(self, tracked: TrackedAmbientSession) -> AmbientReadResult:
        read_result = read_cursor_ide_from_offset(
            Path(tracked.source_path),
            byte_offset=tracked.byte_offset,
        )
        return AmbientReadResult(
            items=read_result.items,
            byte_offset=read_result.byte_offset,
        )
