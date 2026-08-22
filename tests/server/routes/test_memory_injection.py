from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnigent.entities.memory import MemoryCategory
from omnigent.server.routes.sessions.routes_events import _compose_turn_memory


class _MemoryStore:
    def __init__(self, provider: str = "omniharness") -> None:
        self.users: list[str | None] = []
        self.provider = provider

    def list(self, *, user_id: str | None) -> list[MemoryCategory]:
        self.users.append(user_id)
        return [
            MemoryCategory(
                id="a" * 32,
                name="Preferences",
                user_id=user_id,
                display_order=0,
                content="Prefer concise answers.",
                token_count=4,
                created_at=1,
            )
        ]

    def get_max_tokens(self, *, user_id: str | None, default: int) -> int:
        assert user_id == "alice"
        return default

    def get_provider(self, *, user_id: str | None, default: str) -> str:
        assert user_id == "alice"
        return self.provider


@pytest.mark.asyncio
async def test_memory_injected_only_for_omniharness_user_turn() -> None:
    store = _MemoryStore()
    rendered = await _compose_turn_memory(
        store,  # type: ignore[arg-type]
        user_id="alice",
        uses_omniharness=True,
        event_type="message",
        role="user",
        max_tokens=100,
        model=None,
    )
    assert rendered is not None and "<omnigent_memory>" in rendered
    assert store.users == ["alice"]

    for uses_omniharness, role in ((False, "user"), (True, "assistant")):
        assert (
            await _compose_turn_memory(
                store,  # type: ignore[arg-type]
                user_id="alice",
                uses_omniharness=uses_omniharness,
                event_type="message",
                role=role,
                max_tokens=100,
                model=None,
            )
            is None
        )
    assert store.users == ["alice"]


@pytest.mark.asyncio
async def test_file_provider_injects_global_and_project_hierarchy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Registry:
        def get(self, host_id: str) -> object:
            assert host_id == "host-a"
            return SimpleNamespace(owner="alice", workspace_id=0)

        def record_memory_file(
            self,
            host_id: str,
            value: dict[str, object],
            *,
            workspace_id: int,
        ) -> None:
            assert host_id == "host-a"
            assert value["provider"] == "agents"
            assert workspace_id == 0

    async def _read(**kwargs: object) -> dict[str, object]:
        assert kwargs["op"] == "memory.project.read"
        assert kwargs["workspace"] == "/repo/packages/api"
        return {
            "global_file": {
                "provider": "agents",
                "rel_home_path": "AGENTS.md",
                "content": "global instruction",
            },
            "project_files": [
                {"path": "/repo/AGENTS.md", "content": "root instruction"},
                {
                    "path": "/repo/packages/api/AGENTS.md",
                    "content": "specific instruction",
                },
            ],
        }

    monkeypatch.setattr(
        "omnigent.server.routes.sessions.routes_events.read_workspace_from_host",
        _read,
    )
    rendered = await _compose_turn_memory(
        _MemoryStore("agents"),  # type: ignore[arg-type]
        user_id="alice",
        uses_omniharness=True,
        event_type="message",
        role="user",
        max_tokens=1_000,
        model=None,
        host_registry=_Registry(),  # type: ignore[arg-type]
        host_id="host-a",
        workspace="/repo/packages/api",
    )

    assert rendered is not None
    assert 'provider="AGENTS.md"' in rendered
    assert rendered.index("global instruction") < rendered.index("root instruction")
    assert rendered.index("root instruction") < rendered.index("specific instruction")


@pytest.mark.asyncio
async def test_file_provider_does_not_read_another_users_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Registry:
        def get(self, _host_id: str) -> object:
            return SimpleNamespace(owner="bob", workspace_id=0)

    async def _unexpected_read(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("another user's home directory must not be read")

    monkeypatch.setattr(
        "omnigent.server.routes.sessions.routes_events.read_workspace_from_host",
        _unexpected_read,
    )
    rendered = await _compose_turn_memory(
        _MemoryStore("agents"),  # type: ignore[arg-type]
        user_id="alice",
        uses_omniharness=True,
        event_type="message",
        role="user",
        max_tokens=1_000,
        model=None,
        host_registry=_Registry(),  # type: ignore[arg-type]
        host_id="host-a",
        workspace="/repo",
    )
    assert rendered is not None
    assert "Read and follow selected AGENTS.md files" in rendered
