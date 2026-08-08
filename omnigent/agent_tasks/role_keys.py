"""Validation and metadata for glossary role profile keys."""

from __future__ import annotations

import re

from omnigent.errors import ErrorCode, OmnigentError

MANAGER_ROLE_PREFIX = "manager:"
MANAGER_DEFAULT_ROLE_KEY = "manager:default"
WORKER_ROLE_PREFIX = "worker:"
WORKER_DEFAULT_ROLE_KEY = "worker:default"
TASK_BROKER_ROLE_KEY = "broker"
TASK_SECRETARY_ROLE_KEY = "secretary"

SYSTEM_ROLE_KEYS: frozenset[str] = frozenset(
    {
        TASK_BROKER_ROLE_KEY,
        TASK_SECRETARY_ROLE_KEY,
        MANAGER_DEFAULT_ROLE_KEY,
        WORKER_DEFAULT_ROLE_KEY,
    }
)

_TEMPLATE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def is_manager_role_key(role: str) -> bool:
    return role.startswith(MANAGER_ROLE_PREFIX)


def is_worker_role_key(role: str) -> bool:
    return role.startswith(WORKER_ROLE_PREFIX)


def is_template_role_key(role: str) -> bool:
    return is_manager_role_key(role) or is_worker_role_key(role)


def is_system_role_key(role: str) -> bool:
    return role in SYSTEM_ROLE_KEYS


def is_deletable_role_key(role: str) -> bool:
    return is_template_role_key(role) and not is_system_role_key(role)


def default_role_key_for_prefix(prefix: str) -> str:
    if prefix == MANAGER_ROLE_PREFIX:
        return MANAGER_DEFAULT_ROLE_KEY
    if prefix == WORKER_ROLE_PREFIX:
        return WORKER_DEFAULT_ROLE_KEY
    raise OmnigentError(
        f"Unsupported template role prefix: {prefix}",
        code=ErrorCode.INVALID_INPUT,
    )


def role_profile_title(role: str) -> str:
    if role == TASK_BROKER_ROLE_KEY:
        return "Task broker"
    if role == TASK_SECRETARY_ROLE_KEY:
        return "Task secretary"
    if role == MANAGER_DEFAULT_ROLE_KEY:
        return "Task manager (default)"
    if role == WORKER_DEFAULT_ROLE_KEY:
        return "Task worker (default)"
    if is_manager_role_key(role):
        slug = role[len(MANAGER_ROLE_PREFIX) :]
        return f"Task manager ({slug})"
    if is_worker_role_key(role):
        slug = role[len(WORKER_ROLE_PREFIX) :]
        return f"Task worker ({slug})"
    return role


def normalize_template_slug(slug: str) -> str:
    normalized = slug.strip().lower()
    if not _TEMPLATE_SLUG_RE.match(normalized):
        raise OmnigentError(
            "Role slug must be 1-32 lowercase letters, digits, or hyphens",
            code=ErrorCode.INVALID_INPUT,
        )
    if normalized == "default":
        raise OmnigentError(
            "Role slug 'default' is reserved",
            code=ErrorCode.INVALID_INPUT,
        )
    return normalized


def manager_role_key_from_slug(slug: str) -> str:
    return f"{MANAGER_ROLE_PREFIX}{normalize_template_slug(slug)}"


def worker_role_key_from_slug(slug: str) -> str:
    return f"{WORKER_ROLE_PREFIX}{normalize_template_slug(slug)}"


def resolve_template_defaults_role_key(role: str) -> str | None:
    if role in SYSTEM_ROLE_KEYS:
        return role
    if is_manager_role_key(role):
        return MANAGER_DEFAULT_ROLE_KEY
    if is_worker_role_key(role):
        return WORKER_DEFAULT_ROLE_KEY
    return None


def normalize_role_profile_key(role: str) -> str:
    normalized = role.strip().lower()
    if normalized in SYSTEM_ROLE_KEYS:
        return normalized
    if is_manager_role_key(normalized):
        slug = normalized[len(MANAGER_ROLE_PREFIX) :]
        normalize_template_slug(slug)
        return normalized
    if is_worker_role_key(normalized):
        slug = normalized[len(WORKER_ROLE_PREFIX) :]
        normalize_template_slug(slug)
        return normalized
    raise OmnigentError(
        f"Unsupported task agent role: {role}",
        code=ErrorCode.NOT_FOUND,
    )


def is_editable_role_profile_key(role: str) -> bool:
    try:
        normalized = normalize_role_profile_key(role)
    except OmnigentError:
        return False
    return normalized in SYSTEM_ROLE_KEYS or is_template_role_key(normalized)
