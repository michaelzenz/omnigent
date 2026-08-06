"""Per-user task agent role configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UserTaskRoleProfile:
    """
    Task agent settings and live session binding for one user and role.

    :param user_id: Owning user identifier.
    :param role: Task agent role, e.g. ``"broker"`` or ``"secretary"``.
    :param agent_profile_id: Agent profile to spawn for this role.
    :param harness: Brain harness override, e.g. ``"cursor"``.
    :param model: Model override, e.g. ``"composer-2.5"``.
    :param conversation_id: Live role session, or ``None`` before spawn.
    :param host_id: Default host for role/manager bootstrap.
    :param workspace: Default workspace path on ``host_id``.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    user_id: str
    role: str
    agent_profile_id: str
    harness: str
    model: str
    created_at: int
    conversation_id: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    updated_at: int | None = None
