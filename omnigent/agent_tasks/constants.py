"""Shared defaults for managed task agents."""

from __future__ import annotations

from omnigent.errors import ErrorCode, OmnigentError

# Headless ``cursor`` harness (``cursor-sdk``). Chat-first embeds like Puppy Garden
# need the SDK path so agent prompts and ``is_meta`` manual context reach the model.
DEFAULT_TASK_HARNESS = "cursor"
DEFAULT_TASK_MODEL = "composer-2.5"
DEFAULT_TASK_WORKSPACE = "~/"

AUTO_ROUTE_MIN_CONFIDENCE = 0.6
AUTO_ROUTE_MIN_MARGIN = 0.15
AUTO_ROUTE_MAX_CANDIDATES = 10

UNRECONCILED_EVENT_STATES = frozenset({"routed"})
GROUPING_EVENT_STATES = frozenset({"awaiting_grouping", "grouping_proposed"})
DISPATCHABLE_ITEM_STATES = frozenset({"awaiting_user_ack", "approved"})


def resolve_task_harness(harness: str) -> str:
    """Return a runnable Cursor harness for managed task agents."""
    if harness == "cursor-native":
        return harness
    if harness != "cursor":
        return harness
    from omnigent.onboarding.cursor_auth import cursor_sdk_installed
    from omnigent.onboarding.extra_install import extra_install_display

    if cursor_sdk_installed():
        return harness
    raise OmnigentError(
        "Task agents use the headless Cursor harness, which requires the "
        f"'cursor-sdk' package. Install it with: {extra_install_display('cursor')}",
        code=ErrorCode.INVALID_INPUT,
    )
