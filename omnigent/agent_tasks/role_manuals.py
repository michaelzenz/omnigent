"""Per-turn instructions for GlobalHub role sessions.

The server attaches a role session's manual to every turn as per-request
instructions, so the manual is never stored in the session's history.
"""

from __future__ import annotations

from typing import Any

from omnigent.agent_tasks.roles import BuiltinRole, role_definition
from omnigent.stores.user_settings_store import open_user_settings_store


def role_turn_instructions(
    role: BuiltinRole,
    session_id: str,
    conversation_store: Any,
    request_instructions: object,
) -> str:
    """Return a role session's instructions for one turn. Blocking.

    :param role: The session's role, from
        :func:`omnigent.agent_tasks.roles.session_role`.
    :param session_id: The role session, e.g. ``"conv_abc123"``.
    :param conversation_store: The server's conversation store. It answers the
        session's owner and locates the owner's settings.
    :param request_instructions: Instructions the forwarded request already
        carries, e.g. ``None``; kept after the manual when non-blank.
    :returns: The owner's manual for the role (their override, else the
        packaged default), followed by any request instructions.
    """
    manual = _owner_manual(role, session_id, conversation_store)
    if isinstance(request_instructions, str) and request_instructions.strip():
        return f"{manual}\n\n{request_instructions}"
    return manual


def _owner_manual(role: BuiltinRole, session_id: str, conversation_store: Any) -> str:
    store = open_user_settings_store(conversation_store)
    if store is None:
        return role.default_manual()
    # Without an owner grant (single-user mode) the local user's settings apply.
    owner = conversation_store.get_session_owner(session_id, owner_only=True)
    overrides = store.get_globalhub_role_overrides(role.key, user_id=owner)
    return role_definition(role, overrides).manual
