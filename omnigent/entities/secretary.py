"""Per-user secretary agent configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UserSecretaryProfile:
    """
    Secretary agent settings and live session binding for one user.

    :param user_id: Owning user identifier.
    :param agent_id: Secretary agent template to spawn.
    :param harness: Brain harness override, e.g. ``"cursor"``.
    :param model: Model override, e.g. ``"composer-2.5"``.
    :param conversation_id: Live secretary session, or ``None`` before spawn.
    :param host_id: Default host for secretary/manager bootstrap.
    :param workspace: Default workspace path on ``host_id``.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    user_id: str
    agent_id: str
    harness: str
    model: str
    created_at: int
    conversation_id: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    updated_at: int | None = None
