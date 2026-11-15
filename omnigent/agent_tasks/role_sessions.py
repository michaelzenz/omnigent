"""Each user's GlobalHub role sessions, created on the user's GlobalHub host.

A role session is a plain session row bound to the role's built-in agent. Its
runner starts with the first message, the same way a session whose runner
stopped gets a new one.
"""

from __future__ import annotations

import asyncio
import uuid
import weakref
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.roles import BuiltinRole, role_definition, session_role
from omnigent.agent_tasks.session_labels import ROLE_LABEL
from omnigent.entities import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_OWNER
from omnigent.stores.manager_store import ManagerStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

GLOBALHUB_PROJECT_NAME = "GlobalHub"

# Serializes ensures per user and role so concurrent callers cannot both create
# a session. Process-local, so it assumes a single server replica.
_ensure_locks: weakref.WeakValueDictionary[tuple[str | None, str], asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


@dataclass(frozen=True)
class RoleSessionStores:
    """The stores GlobalHub role sessions are created with.

    :param conversation_store: Holds the role sessions.
    :param agent_store: Finds each role's built-in agent by name.
    :param user_settings_store: Holds each user's GlobalHub placement, role
        overrides, and singleton role sessions.
    :param project_store: Files role sessions into the user's GlobalHub
        project; ``None`` leaves them unfiled.
    :param permission_store: Grants the user ownership of their role sessions;
        ``None`` on servers without permissions.
    """

    conversation_store: Any
    agent_store: Any
    user_settings_store: UserSettingsStore
    project_store: Any | None = None
    permission_store: Any | None = None


def require_globalhub_placement(
    user_settings_store: UserSettingsStore, user_id: str | None
) -> GlobalHubPlacement:
    """Return where the user's role sessions run. Blocking.

    :raises OmnigentError: ``INVALID_INPUT`` when the user has not chosen a
        GlobalHub host and workspace.
    """
    placement = user_settings_store.get_globalhub_placement(user_id=user_id)
    if placement is None:
        raise OmnigentError(
            "Choose a GlobalHub host and workspace first (PUT /v1/settings/globalhub).",
            code=ErrorCode.INVALID_INPUT,
        )
    return placement


def create_role_session(
    role: BuiltinRole,
    *,
    user_id: str | None,
    placement: GlobalHubPlacement,
    title: str,
    stores: RoleSessionStores,
) -> Conversation:
    """Create a session for ``role`` owned by the user. Blocking.

    :param role: The role the session serves.
    :param user_id: The owner, or ``None`` in single-user mode.
    :param placement: The host and workspace the session runs on.
    :param title: The session title, e.g. ``"Task broker"``.
    :param stores: Where the session and its metadata live.
    :returns: The new session, labeled with the role and running the user's
        model for it.
    :raises OmnigentError: ``INTERNAL_ERROR`` when the role's agent is missing.
    """
    agent = stores.agent_store.get_by_name(role.agent_name)
    if agent is None:
        raise OmnigentError(
            f"GlobalHub agent {role.agent_name!r} is not installed",
            code=ErrorCode.INTERNAL_ERROR,
        )
    overrides = stores.user_settings_store.get_globalhub_role_overrides(role.key, user_id=user_id)
    conversation = stores.conversation_store.create_conversation(
        agent_id=agent.id,
        title=title,
        host_id=placement.host_id,
        workspace=placement.workspace,
        project_id=ensure_globalhub_project(stores.project_store, user_id),
        labels={ROLE_LABEL: role.session_label},
        model_override=role_definition(role, overrides).model,
    )
    if stores.permission_store is not None and user_id is not None:
        stores.permission_store.ensure_user(user_id)
        stores.permission_store.grant(user_id, conversation.id, LEVEL_OWNER)
    return conversation


async def ensure_role_session(
    role: BuiltinRole, *, user_id: str | None, stores: RoleSessionStores
) -> tuple[Conversation, bool]:
    """Return the user's session for a singleton role, creating it when needed.

    A new session replaces the recorded one when that one was deleted or
    archived, or when the user moved GlobalHub to another host or workspace.

    :param role: A singleton role, e.g. the broker.
    :param user_id: The owner, or ``None`` in single-user mode.
    :param stores: Where the session and its metadata live.
    :returns: The session, and whether this call created it.
    :raises ValueError: When ``role`` is not a singleton.
    :raises OmnigentError: ``INVALID_INPUT`` without a GlobalHub placement.
    """
    if not role.singleton:
        raise ValueError(f"role {role.key!r} has a session per manager, not one per user")
    lock = _ensure_locks.setdefault((user_id, role.key), asyncio.Lock())
    async with lock:
        return await asyncio.to_thread(_ensure_role_session, role, user_id, stores)


def ensure_globalhub_project(project_store: Any | None, user_id: str | None) -> str | None:
    """Return the user's GlobalHub project id, creating the project when missing.

    :param project_store: The server's project store, or ``None``.
    :param user_id: The owner, or ``None`` in single-user mode.
    :returns: The project id, or ``None`` when the server has no project store.
    """
    if project_store is None:
        return None
    existing = _find_globalhub_project(project_store, user_id)
    if existing is not None:
        return existing
    try:
        return project_store.create(uuid.uuid4().hex, GLOBALHUB_PROJECT_NAME, user_id).id
    except OmnigentError as exc:
        # Another request created the project first; use theirs.
        found = (
            _find_globalhub_project(project_store, user_id)
            if exc.code == ErrorCode.ALREADY_EXISTS
            else None
        )
        if found is None:
            raise
        return found


def is_role_session_current(
    session: Conversation,
    *,
    user_id: str | None,
    stores: RoleSessionStores,
    managers: ManagerStore,
) -> bool:
    """Whether ``session`` still serves its role, so messages for the role may go to it. Blocking.

    A broker or custodian session must be the user's current one for that role;
    a manager session must belong to one of the user's managers.
    """
    role = session_role(session.labels)
    if role is None or session.archived:
        return False
    if not role.singleton:
        return managers.get_by_conversation_id(session.id, owner_user_id=user_id) is not None
    current = current_role_session(role, user_id=user_id, stores=stores)
    return current is not None and current.id == session.id


def current_role_session(
    role: BuiltinRole, *, user_id: str | None, stores: RoleSessionStores
) -> Conversation | None:
    """Return the user's session for a singleton role without creating one. Blocking.

    ``None`` when the user has no GlobalHub placement, or when the recorded
    session was deleted, archived, or left behind when GlobalHub moved.
    """
    placement = stores.user_settings_store.get_globalhub_placement(user_id=user_id)
    return _current_role_session(role, user_id, stores, placement) if placement else None


def _current_role_session(
    role: BuiltinRole,
    user_id: str | None,
    stores: RoleSessionStores,
    placement: GlobalHubPlacement,
) -> Conversation | None:
    """Return the recorded session for a singleton role unless it was replaced.

    A recorded session is replaced when it was deleted or archived, or when the
    user moved GlobalHub to another host or workspace.
    """
    session_id = stores.user_settings_store.get_globalhub_role_session(role.key, user_id=user_id)
    current = stores.conversation_store.get_conversation(session_id) if session_id else None
    if (
        current is not None
        and not current.archived
        and current.host_id == placement.host_id
        and current.workspace == placement.workspace
    ):
        return current
    return None


def _ensure_role_session(
    role: BuiltinRole, user_id: str | None, stores: RoleSessionStores
) -> tuple[Conversation, bool]:
    settings = stores.user_settings_store
    placement = require_globalhub_placement(settings, user_id)
    current = _current_role_session(role, user_id, stores, placement)
    if current is not None:
        return current, False
    created = create_role_session(
        role, user_id=user_id, placement=placement, title=role.title, stores=stores
    )
    settings.save_globalhub_role_session(role.key, created.id, user_id=user_id)
    return created, True


def _find_globalhub_project(project_store: Any, user_id: str | None) -> str | None:
    return next(
        (
            project.id
            for project in project_store.list(user_id=user_id)
            if project.name == GLOBALHUB_PROJECT_NAME
        ),
        None,
    )
