"""Remote SSH Cursor IDE transcript polling."""

from __future__ import annotations

from omnigent.host.polling.pollers.ambient_state import TrackedAmbientSession
from omnigent.host.polling.pollers.ambient_subpoller import AmbientReadResult, RemoteSubPollerBase
from omnigent.session_import.cursor_ide import (
    initial_cursor_ide_byte_offset,
    load_cursor_ide_session,
    read_cursor_ide_from_offset,
)
from omnigent.session_import.models import LocalSessionImport
from omnigent.ssh_remote import (
    RemoteCursorIdeTranscript,
    ssh_remote_cursor_ide_transcripts,
    ssh_remote_file_to_tempfile,
    ssh_remote_path_exists,
)


def _workspace_from_slug(slug: str) -> str | None:
    if not slug:
        return None
    return "/" + slug.replace("-", "/")


class CursorIdeRemoteSubPoller(RemoteSubPollerBase):
    """Scan Cursor IDE transcripts on one configured SSH host."""

    def _source_label(self) -> str:
        return "Cursor IDE"

    def _import_source(self):
        return "cursor-ide"

    async def _list_remote_discoveries(self) -> list[RemoteCursorIdeTranscript]:
        return await ssh_remote_cursor_ide_transcripts(self._profile)

    async def _remote_source_exists(self, source_path: str) -> bool:
        return await ssh_remote_path_exists(self._profile, source_path)

    def _discovery_session_key(self, discovery: RemoteCursorIdeTranscript) -> str:
        return discovery.transcript_id

    def _discovery_source_path(self, discovery: RemoteCursorIdeTranscript) -> str:
        return discovery.path

    def _discovery_mtime_ms(self, discovery: RemoteCursorIdeTranscript) -> int:
        return discovery.mtime_ms

    async def _prepare_import(
        self,
        discovery: RemoteCursorIdeTranscript,
    ) -> tuple[LocalSessionImport, int] | None:
        workspace = _workspace_from_slug(discovery.workspace_slug)
        temp_path = await ssh_remote_file_to_tempfile(self._profile, discovery.path)
        try:
            imported = load_cursor_ide_session(temp_path, workspace=workspace)
            return imported, initial_cursor_ide_byte_offset(temp_path)
        except Exception:
            return None
        finally:
            temp_path.unlink(missing_ok=True)

    async def _read_updates(
        self,
        discovery: RemoteCursorIdeTranscript,
        *,
        tracked: TrackedAmbientSession,
    ) -> AmbientReadResult:
        temp_path = await ssh_remote_file_to_tempfile(self._profile, discovery.path)
        try:
            read_result = read_cursor_ide_from_offset(temp_path, byte_offset=tracked.byte_offset)
        finally:
            temp_path.unlink(missing_ok=True)
        return AmbientReadResult(
            items=read_result.items,
            byte_offset=read_result.byte_offset,
        )
