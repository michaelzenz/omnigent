"""Server-owned SSH connection entities and validation."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

_ALIAS_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
_PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_PACKAGE_INDEX_URL_RE = re.compile(r"^https://[^\s]+$")


@dataclass(frozen=True)
class SshSettings:
    """Workspace SSH provisioning settings."""

    package_index_url: str | None = None
    npm_registry_url: str | None = None
    remote_namespace: str = ""


@dataclass(frozen=True)
class SshConnectionProfile:
    """One server-managed SSH config alias."""

    id: str
    label: str
    alias: str
    created_at: str
    owner: str | None = None


def validate_ssh_alias(alias: str) -> str | None:
    """Return an error message when *alias* is invalid."""
    trimmed = alias.strip()
    if not trimmed:
        return "SSH alias is required"
    if not _ALIAS_RE.match(trimmed):
        return "SSH alias contains invalid characters"
    return None


def validate_package_index_url(url: str) -> str | None:
    """Return an error when *url* is not a safe HTTPS package index."""
    trimmed = url.strip()
    if not trimmed:
        return None
    if len(trimmed) > 512:
        return "Package index URL is too long"
    if not _PACKAGE_INDEX_URL_RE.fullmatch(trimmed):
        return "Package index URL must be an HTTPS URL"
    return None


def validate_npm_registry_url(url: str) -> str | None:
    """Return an error when *url* is not a safe HTTPS npm registry."""
    trimmed = url.strip()
    if not trimmed:
        return None
    if len(trimmed) > 512:
        return "npm registry URL is too long"
    if not _PACKAGE_INDEX_URL_RE.fullmatch(trimmed):
        return "npm registry URL must be an HTTPS URL"
    return None


def validate_ssh_connection_id(connection_id: str) -> str | None:
    """Return an error when an id is unsafe for filenames and remote paths."""
    if not _PROFILE_ID_RE.fullmatch(connection_id.strip()):
        return "SSH connection id contains invalid characters"
    return None


def new_ssh_connection_id() -> str:
    """Generate a stable id for a new SSH connection profile."""
    return uuid.uuid4().hex


def profile_to_api_dict(profile: SshConnectionProfile) -> dict[str, object]:
    """Serialize one profile for REST responses."""
    return {
        "id": profile.id,
        "label": profile.label,
        "alias": profile.alias,
        "created_at": profile.created_at,
    }
