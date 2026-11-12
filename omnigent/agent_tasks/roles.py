"""The GlobalHub background roles and each user's definition of them."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from omnigent.agent_tasks.session_labels import (
    BROKER_ROLE_VALUE,
    CUSTODIAN_ROLE_VALUE,
    MANAGER_ROLE_VALUE,
    ROLE_LABEL,
)

BROKER_ROLE_KEY = "broker"
MANAGER_ROLE_KEY = "manager:default"
CUSTODIAN_ROLE_KEY = "custodian"
#: Fields a user may override; a field without an override uses the role's default.
ROLE_OVERRIDE_FIELDS = ("description", "model", "manual")


@dataclass(frozen=True)
class BuiltinRole:
    """A GlobalHub role and the defaults a user's definition falls back to.

    :param key: Role key in the API, e.g. ``"manager:default"``.
    :param kind: Role family, e.g. ``"manager"``.
    :param title: Display name, e.g. ``"Task manager"``.
    :param description: Default summary of what the role does.
    :param session_label: ``ROLE_LABEL`` value on the role's sessions, e.g.
        ``"task_manager"``.
    :param manual_file: Packaged default manual, e.g. ``"task_manager.md"``.
    :param agent_name: Built-in agent the role's sessions run, e.g. ``"onih-pmv2"``.
    :param singleton: Whether each user has exactly one session for the role.
        Managers are not singletons: each manager gets its own session.
    """

    key: str
    kind: str
    title: str
    description: str
    session_label: str
    manual_file: str
    agent_name: str
    singleton: bool

    def default_manual(self) -> str:
        """Return the manual packaged with omnigent for this role."""
        return _read_packaged_manual(self.manual_file)


@dataclass(frozen=True)
class RoleDefinition:
    """One user's effective definition of a role.

    :param role: The role being defined.
    :param description: What the role does.
    :param model: Model for the role's sessions, or ``None`` for the role
        agent's own model.
    :param manual: Manual attached to every turn of the role's sessions.
    :param customized: Fields the user overrode, e.g. ``("manual",)``.
    """

    role: BuiltinRole
    description: str
    model: str | None
    manual: str
    customized: tuple[str, ...]


BUILTIN_ROLES = (
    BuiltinRole(
        key=BROKER_ROLE_KEY,
        kind="broker",
        title="Task broker",
        description="Triages incoming events and routes work to tasks.",
        session_label=BROKER_ROLE_VALUE,
        manual_file="task_broker.md",
        agent_name="onih-pmv2",
        singleton=True,
    ),
    BuiltinRole(
        key=MANAGER_ROLE_KEY,
        kind="manager",
        title="Task manager",
        description="Owns a task, plans work, and supervises Workers.",
        session_label=MANAGER_ROLE_VALUE,
        manual_file="task_manager.md",
        agent_name="onih-pmv2",
        singleton=False,
    ),
    BuiltinRole(
        key=CUSTODIAN_ROLE_KEY,
        kind="custodian",
        title="Task custodian",
        description="Maintains session hygiene and executes system-notice actions.",
        session_label=CUSTODIAN_ROLE_VALUE,
        manual_file="task_custodian.md",
        agent_name="onih-custodian",
        singleton=True,
    ),
)
_ROLES_BY_KEY = {role.key: role for role in BUILTIN_ROLES}
_ROLES_BY_SESSION_LABEL = {role.session_label: role for role in BUILTIN_ROLES}


def builtin_role(key: str) -> BuiltinRole | None:
    """Return the role whose API key is ``key``, e.g. ``"custodian"``, or ``None``."""
    return _ROLES_BY_KEY.get(key)


def session_role(labels: Mapping[str, str] | None) -> BuiltinRole | None:
    """Return the role a session's ``ROLE_LABEL`` names, or ``None`` for other sessions."""
    value = labels.get(ROLE_LABEL) if labels else None
    return _ROLES_BY_SESSION_LABEL.get(value) if value is not None else None


def role_definition(role: BuiltinRole, overrides: Mapping[str, str]) -> RoleDefinition:
    """Apply a user's saved overrides to a role's defaults.

    :param role: The role being defined.
    :param overrides: The user's saved values by field name, e.g.
        ``{"manual": "..."}``. Fields outside ``ROLE_OVERRIDE_FIELDS`` are ignored.
    :returns: The user's effective definition.
    """
    return RoleDefinition(
        role=role,
        description=overrides.get("description", role.description),
        model=overrides.get("model"),
        manual=overrides.get("manual") or role.default_manual(),
        customized=tuple(field for field in ROLE_OVERRIDE_FIELDS if field in overrides),
    )


@cache
def _read_packaged_manual(file_name: str) -> str:
    return files("omnigent.resources.role_manuals").joinpath(file_name).read_text(encoding="utf-8")
