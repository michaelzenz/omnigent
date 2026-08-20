"""Prompt-profile selection shared by session routes and runner dispatch."""

from __future__ import annotations

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores import PromptProfileStore

PROMPT_PROFILE_HARNESS = "openai-agents"


def load_prompt_profile_instructions(
    profile_id: str,
    prompt_profile_store: PromptProfileStore,
    *,
    require_selectable: bool,
) -> str | None:
    """Load instructions while enforcing new- versus existing-selection rules."""
    profile = prompt_profile_store.get(profile_id)
    if profile is None or profile.archived or (require_selectable and not profile.enabled):
        raise OmnigentError(
            f"Profile not found or unavailable: {profile_id!r}",
            code=ErrorCode.NOT_FOUND,
        )
    return profile.instructions
