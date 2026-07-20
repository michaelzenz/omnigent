"""Local filesystem Codex rollout polling."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.polling.pollers.ambient_state import TrackedAmbientSession
from omnigent.host.polling.pollers.ambient_subpoller import AmbientReadResult, LocalAmbientSubPollerBase
from omnigent.session_import.codex_rollout import (
    active_codex_rollout_path,
    iter_codex_rollout_paths,
    read_codex_rollout_from_offset,
    thread_id_from_rollout_path,
)
from omnigent.session_import.local import load_codex_session
from omnigent.session_import.models import LocalSessionImport


class CodexLocalSubPoller(LocalAmbientSubPollerBase):
    """Scan ``~/.codex`` rollouts on the host machine."""

    def __init__(self, *, codex_home: Path) -> None:
        super().__init__()
        self._codex_home = codex_home

    def _source_label(self) -> str:
        return "Codex"

    def _import_source(self):
        return "codex"

    def _iter_discoveries(self):
        return iter_codex_rollout_paths(self._codex_home)

    def _discovery_session_key(self, discovery: Path) -> str:
        thread_id = thread_id_from_rollout_path(discovery)
        if thread_id is None:
            raise ValueError(f"invalid rollout path: {discovery}")
        return thread_id

    def _discovery_source_path(self, discovery: Path) -> str:
        return str(discovery)

    def _discovery_mtime_ms(self, discovery: Path) -> int:
        return int(discovery.stat().st_mtime * 1000)

    async def _local_source_missing(self, tracked: TrackedAmbientSession) -> bool:
        if active_codex_rollout_path(self._codex_home, tracked.session_key) is not None:
            return False
        return not Path(tracked.source_path).is_file()

    def _load_import(self, discovery: Path) -> LocalSessionImport:
        return load_codex_session(
            self._discovery_session_key(discovery),
            codex_home=self._codex_home,
        )

    def _initial_byte_offset(self, discovery: Path, imported: LocalSessionImport) -> int:
        return discovery.stat().st_size

    def _read_updates(self, tracked: TrackedAmbientSession) -> AmbientReadResult:
        read_result = read_codex_rollout_from_offset(
            Path(tracked.source_path),
            byte_offset=tracked.byte_offset,
            turn_id=tracked.turn_id,
            workspace=tracked.workspace,
        )
        return AmbientReadResult(
            items=read_result.items,
            byte_offset=read_result.byte_offset,
            turn_id=read_result.turn_id,
            workspace=read_result.workspace,
        )

    async def _ensure_tracked(self, client, *, state, discovery: Path):
        if thread_id_from_rollout_path(discovery) is None:
            return None
        return await super()._ensure_tracked(client, state=state, discovery=discovery)
