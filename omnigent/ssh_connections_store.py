"""SSH connection profiles stored in ``~/.omnigent/config.yaml``."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH

_CONFIG_KEY = "ssh_connections"
_SETTINGS_KEY = "ssh_settings"
_ALIAS_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
_PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_PACKAGE_INDEX_URL_RE = re.compile(r"^https://[^\s]+$")


@dataclass(frozen=True)
class SshSettings:
    """Server-side SSH host installation settings."""

    package_index_url: str | None = None


@dataclass(frozen=True)
class SshConnectionProfile:
    """One SSH config Host alias the host daemon may use."""

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
    """Return an error when a package index URL is not a safe HTTPS endpoint."""
    trimmed = url.strip()
    if not trimmed:
        return None
    if len(trimmed) > 512:
        return "Package index URL is too long"
    if not _PACKAGE_INDEX_URL_RE.fullmatch(trimmed):
        return "Package index URL must be an HTTPS URL"
    return None


def validate_ssh_connection_id(connection_id: str) -> str | None:
    """Return an error when an id isn't safe for filenames and remote paths."""
    if not _PROFILE_ID_RE.fullmatch(connection_id.strip()):
        return "SSH connection id contains invalid characters"
    return None


def _parse_profile(raw: object) -> SshConnectionProfile | None:
    if not isinstance(raw, dict):
        return None
    profile_id = raw.get("id")
    label = raw.get("label")
    alias = raw.get("alias")
    created_at = raw.get("created_at")
    owner = raw.get("owner")
    if not all(isinstance(value, str) for value in (profile_id, label, alias, created_at)):
        return None
    if validate_ssh_connection_id(profile_id) is not None:
        return None
    if validate_ssh_alias(alias) is not None:
        return None
    return SshConnectionProfile(
        id=profile_id,
        label=label.strip(),
        alias=alias.strip(),
        created_at=created_at,
        owner=owner.strip() if isinstance(owner, str) and owner.strip() else None,
    )


def _load_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    try:
        with config_path.open(encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
    except OSError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def read_ssh_settings(config_path: Path = CONFIG_PATH) -> SshSettings:
    """Load SSH installation settings from config.yaml."""
    cfg = _load_config(config_path)
    raw_settings = cfg.get(_SETTINGS_KEY)
    if not isinstance(raw_settings, dict):
        return SshSettings()
    raw_url = raw_settings.get("package_index_url")
    if not isinstance(raw_url, str) or not raw_url.strip():
        return SshSettings()
    if validate_package_index_url(raw_url) is not None:
        return SshSettings()
    return SshSettings(package_index_url=raw_url.strip())


def write_ssh_settings(
    settings: SshSettings,
    *,
    config_path: Path = CONFIG_PATH,
) -> None:
    """Persist SSH installation settings into config.yaml."""
    cfg = _load_config(config_path)
    if settings.package_index_url is None:
        raw_settings = cfg.get(_SETTINGS_KEY)
        if isinstance(raw_settings, dict):
            raw_settings = dict(raw_settings)
            raw_settings.pop("package_index_url", None)
            if raw_settings:
                cfg[_SETTINGS_KEY] = raw_settings
            else:
                cfg.pop(_SETTINGS_KEY, None)
    else:
        cfg[_SETTINGS_KEY] = {"package_index_url": settings.package_index_url}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(cfg, handle, default_flow_style=False, sort_keys=True)


def read_ssh_connections(config_path: Path = CONFIG_PATH) -> list[SshConnectionProfile]:
    """Load SSH connection profiles from config.yaml."""
    cfg = _load_config(config_path)
    raw_list = cfg.get(_CONFIG_KEY)
    if not isinstance(raw_list, list):
        return []
    profiles: list[SshConnectionProfile] = []
    for entry in raw_list:
        profile = _parse_profile(entry)
        if profile is not None:
            profiles.append(profile)
    return profiles


def write_ssh_connections(
    profiles: list[SshConnectionProfile],
    *,
    config_path: Path = CONFIG_PATH,
) -> None:
    """Persist SSH connection profiles into config.yaml."""
    cfg = _load_config(config_path)
    cfg[_CONFIG_KEY] = [asdict(profile) for profile in profiles]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(cfg, handle, default_flow_style=False, sort_keys=True)


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
