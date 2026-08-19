from __future__ import annotations

from omnigent.entities.memory import MemoryCategory
from omnigent.memory import compose_memory, count_memory_tokens


def _category(name: str, content: str, order: int) -> MemoryCategory:
    return MemoryCategory(
        id=name.lower(),
        name=name,
        user_id=None,
        display_order=order,
        content=content,
        token_count=count_memory_tokens(content),
        created_at=1,
    )


def test_compose_memory_orders_categories() -> None:
    result = compose_memory(
        [_category("Later", "second", 1), _category("Earlier", "first", 0)],
        1_000,
    )
    assert result is not None
    assert result.startswith("<omnigent_memory>")
    assert result.index("Earlier") < result.index("Later")
    assert result.endswith("</omnigent_memory>")


def test_compose_memory_truncates_to_budget() -> None:
    result = compose_memory([_category("Long", "word " * 100, 0)], 12)
    assert result is not None
    assert count_memory_tokens(result) <= 12
