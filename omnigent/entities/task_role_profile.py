"""Task agent role definitions and the sessions bound to them."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskRoleProfile:
    """
    A named deployment of an agent profile for one task role.

    A role answers *who* runs (``agent_profile_id``), *where* it runs
    (``host_id``, ``workspace``) and *what drives it* (``harness``,
    ``model``). Everything but the key and kind is optional: roles defined
    outside Omnigent carry their own metadata and leave the rest unset, and
    ``model`` is unset whenever the harness resolves its own model (e.g.
    Codex, OpenCode).

    :param role: Role key, e.g. ``"broker"`` or ``"worker:reviewer"``.
    :param kind: Role family — manager, worker, broker, secretary, external.
    :param agent_profile_id: Agent profile to spawn for this role.
    :param harness: Brain harness, e.g. ``"cursor"``.
    :param model: Model override, e.g. ``"composer-2.5"``.
    :param host_id: Default host for bootstrap.
    :param workspace: Default workspace path on ``host_id``.
    :param description: What the role specializes in; surfaced to the manager
        when it lists worker roles to pick one for a new lane. ``None`` for
        externally-defined roles that carry their own metadata.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    role: str
    kind: str
    created_at: int
    agent_profile_id: str | None = None
    harness: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    description: str | None = None
    updated_at: int | None = None


@dataclass
class UserRoleSession:
    """
    One user's live conversation with a singleton role.

    Roles instantiated per task or per worker lane keep their conversation on
    that binding instead; this covers roles a user talks to directly, such as
    the broker and secretary.

    :param user_id: Owning user identifier.
    :param role: Role key the session belongs to.
    :param conversation_id: Live session, or ``None`` before spawn.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    user_id: str
    role: str
    created_at: int
    conversation_id: str | None = None
    updated_at: int | None = None
