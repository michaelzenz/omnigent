"""Remote SSH Codex rollout polling."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.polling.pollers.ambient_state import TrackedAmbientSession
from omnigent.host.polling.pollers.ambient_subpoller import AmbientReadResult, RemoteSubPollerBase
from omnigent.host.polling.pollers.codex_state import import_codex_session, rollout_is_recent
from omnigent.session_import.codex_rollout import read_codex_rollout_from_offset
from omnigent.session_import.local import load_codex_session_from_rollout
from omnigent.session_import.models import LocalSessionImport
from omnigent.ssh_remote import (
    RemoteCodexRollout,
    ssh_remote_codex_rollouts,
    ssh_remote_path_exists,
    ssh_remote_rollout_to_tempfile,
)


class CodexRemoteSubPoller(RemoteSubPollerBase):
    """Scan Codex rollouts on one configured SSH host."""

    def _source_label(self) -> str:
        return "Codex"

    def _import_source(self):
        return "codex"

    async def _list_remote_discoveries(self) -> list[RemoteCodexRollout]:
        return await ssh_remote_codex_rollouts(self._profile)

    async def _remote_source_exists(self, source_path: str) -> bool:
        return await ssh_remote_path_exists(self._profile, source_path)

    def _discovery_session_key(self, discovery: RemoteCodexRollout) -> str:
        from omnigent.session_import.codex_rollout import thread_id_from_rollout_path

        thread_id = thread_id_from_rollout_path(Path(discovery.path))
        if thread_id is None:
            raise ValueError(f"invalid remote rollout path: {discovery.path}")
        return thread_id

    def _discovery_source_path(self, discovery: RemoteCodexRollout) -> str:
        return discovery.path

    def _discovery_mtime_ms(self, discovery: RemoteCodexRollout) -> int:
        return discovery.mtime_ms

    async def _load_import(self, discovery: RemoteCodexRollout) -> LocalSessionImport:
        temp_path = await ssh_remote_rollout_to_tempfile(self._profile, discovery.path)
        try:
            return load_codex_session_from_rollout(temp_path, self._discovery_session_key(discovery))
        finally:
            temp_path.unlink(missing_ok=True)

    def _initial_byte_offset(self, discovery: RemoteCodexRollout, imported: LocalSessionImport) -> int:
        return discovery.size

    async def _read_updates(
        self,
        discovery: RemoteCodexRollout,
        *,
        tracked: TrackedAmbientSession,
    ) -> AmbientReadResult:
        temp_path = await ssh_remote_rollout_to_tempfile(
            self._profile,
            discovery.path,
            byte_offset=tracked.byte_offset,
        )
        try:
            read_result = read_codex_rollout_from_offset(
                temp_path,
                byte_offset=0,
                turn_id=tracked.turn_id,
                workspace=tracked.workspace,
            )
        finally:
            temp_path.unlink(missing_ok=True)
        return AmbientReadResult(
            items=read_result.items,
            byte_offset=tracked.byte_offset + read_result.byte_offset,
            turn_id=read_result.turn_id,
            workspace=read_result.workspace,
        )
