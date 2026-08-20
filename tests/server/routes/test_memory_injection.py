from __future__ import annotations

import pytest

from omnigent.entities.memory import MemoryCategory
from omnigent.server.routes.sessions.routes_events import _compose_turn_memory


class _MemoryStore:
    def __init__(self) -> None:
        self.users: list[str | None] = []

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
