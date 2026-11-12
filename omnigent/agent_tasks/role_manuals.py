"""Manuals for the GlobalHub background roles.

The server attaches a role session's manual to every turn as per-request
instructions, so the manual is never stored in the session's history.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from importlib.resources import files

from omnigent.agent_tasks.session_labels import (
    BROKER_ROLE_VALUE,
    CUSTODIAN_ROLE_VALUE,
    MANAGER_ROLE_VALUE,
    ROLE_LABEL,
)

_DEFAULT_MANUAL_FILES = {
    BROKER_ROLE_VALUE: "task_broker.md",
    MANAGER_ROLE_VALUE: "task_manager.md",
    CUSTODIAN_ROLE_VALUE: "task_custodian.md",
}


def default_role_manual(role: str) -> str | None:
    """Return the packaged manual for ``role``.

    :param role: A role label value, e.g. ``"task_manager"``.
    :returns: The manual's Markdown text, or ``None`` for an unknown role.
    """
    file_name = _DEFAULT_MANUAL_FILES.get(role)
    return _read_packaged_manual(file_name) if file_name is not None else None


def role_turn_instructions(
    labels: Mapping[str, str] | None, request_instructions: object
) -> str | None:
    """Return the per-turn instructions for a role session.

    :param labels: The session's labels; its role comes from ``ROLE_LABEL``.
    :param request_instructions: Instructions the forwarded request already
        carries, e.g. ``None``; kept after the manual when non-blank.
    :returns: The role's manual, followed by any request instructions, or
        ``None`` when the session has no known role.
    """
    role = labels.get(ROLE_LABEL) if labels else None
    manual = default_role_manual(role) if role is not None else None
    if manual is None:
        return None
    if isinstance(request_instructions, str) and request_instructions.strip():
        return f"{manual}\n\n{request_instructions}"
    return manual


@cache
def _read_packaged_manual(file_name: str) -> str:
    return files("omnigent.resources.role_manuals").joinpath(file_name).read_text(encoding="utf-8")
