"""Rendering and token accounting for persistent user memory."""

from __future__ import annotations

from html import escape

import tiktoken

from omnigent.entities.memory import MemoryCategory

DEFAULT_MEMORY_MAX_TOKENS = 20_000


def _encoding(model: str | None = None) -> tiktoken.Encoding:
    if model:
        try:
            return tiktoken.encoding_for_model(model.removeprefix("openai/"))
        except KeyError:
            pass
    return tiktoken.get_encoding("cl100k_base")


def count_memory_tokens(text: str, model: str | None = None) -> int:
    """Count tokens using the model encoding with a stable fallback."""
    return len(_encoding(model).encode(text))


def compose_memory(
    categories: list[MemoryCategory],
    max_tokens: int,
    *,
    model: str | None = None,
) -> str | None:
    """Render ordered categories, truncating the last included category."""
    if max_tokens <= 0:
        return None
    populated = [
        category
        for category in sorted(categories, key=lambda item: (item.display_order, item.id))
        if category.content
    ]
    if not populated:
        return None
    encoding = _encoding(model)
    prefix = "<omnigent_memory>\n"
    suffix = "\n</omnigent_memory>"
    separator = "\n\n"
    rendered: list[str] = []

    for category in populated:
        header = f'<category id="{category.id}" name="{escape(category.name, quote=True)}">\n'
        footer = "\n</category>"
        block = f"{header}{category.content}{footer}"
        candidate = prefix + separator.join([*rendered, block]) + suffix
        if len(encoding.encode(candidate)) <= max_tokens:
            rendered.append(block)
            continue

        content_tokens = encoding.encode(category.content)
        low, high = 0, len(content_tokens)
        best: str | None = None
        while low <= high:
            midpoint = (low + high) // 2
            truncated = encoding.decode(content_tokens[:midpoint])
            partial_block = f"{header}{truncated}{footer}"
            partial = prefix + separator.join([*rendered, partial_block]) + suffix
            if len(encoding.encode(partial)) <= max_tokens:
                best = partial_block
                low = midpoint + 1
            else:
                high = midpoint - 1
        if best is not None:
            rendered.append(best)
        break

    if rendered:
        return prefix + separator.join(rendered) + suffix

    empty = "<omnigent_memory/>"
    return empty if len(encoding.encode(empty)) <= max_tokens else None
