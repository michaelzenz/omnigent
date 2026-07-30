"""Built-in host timer handlers."""

from __future__ import annotations

from omnigent.host.timer.handlers.prompt import PromptTimerHandler
from omnigent.host.timer.protocol import TimerHandler

DEFAULT_TIMER_HANDLERS: dict[str, TimerHandler] = {
    "prompt": PromptTimerHandler(),
}
