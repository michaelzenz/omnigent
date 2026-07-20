"""Remote SSH Cursor CLI chat polling."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.polling.pollers.ambient_state import TrackedAmbientSession
from omnigent.host.polling.pollers.ambient_subpoller import AmbientReadResult, RemoteSubPollerBase
from omnigent.session_import.cursor_cli import (
    initial_cursor_cli_rowid,
    load_cursor_cli_session,
    read_cursor_cli_from_rowid,
)
from omnigent.session_import.models import LocalSessionImport
from omnigent.ssh_remote import (
    RemoteCursorCliChat,
    ssh_remote_cursor_cli_chats,
    ssh_remote_file_to_tempfile,
    ssh_remote_path_exists,
)


class CursorCliRemoteSubPoller(RemoteSubPollerBase):
    """Scan cursor-agent CLI chats on one configured SSH host."""

    def _source_label(self) -> str:
        return "Cursor CLI"

    def _import_source(self):
        return "cursor-cli"

    async def _list_remote_discoveries(self) -> list[RemoteCursorCliChat]:
        return await ssh_remote_cursor_cli_chats(self._profile)

    async def _remote_source_exists(self, source_path: str) -> bool:
        return await ssh_remote_path_exists(self._profile, source_path)

    def _discovery_session_key(self, discovery: RemoteCursorCliChat) -> str:
        return discovery.chat_id

    def _discovery_source_path(self, discovery: RemoteCursorCliChat) -> str:
        return discovery.path

    def _discovery_mtime_ms(self, discovery: RemoteCursorCliChat) -> int:
        return discovery.mtime_ms

    async def _prepare_import(
        self,
        discovery: RemoteCursorCliChat,
    ) -> tuple[LocalSessionImport, int] | None:
        temp_path = await ssh_remote_file_to_tempfile(self._profile, discovery.path)
        try:
            imported = load_cursor_cli_session(temp_path)
            return imported, initial_cursor_cli_rowid(temp_path)
        except Exception:
            return None
        finally:
            temp_path.unlink(missing_ok=True)

    async def _read_updates(
        self,
        discovery: RemoteCursorCliChat,
        *,
        tracked: TrackedAmbientSession,
    ) -> AmbientReadResult:
        temp_path = await ssh_remote_file_to_tempfile(self._profile, discovery.path)
        try:
            read_result = read_cursor_cli_from_rowid(temp_path, last_rowid=tracked.byte_offset)
        finally:
            temp_path.unlink(missing_ok=True)
        return AmbientReadResult(
            items=read_result.items,
            byte_offset=read_result.last_rowid,
        )
