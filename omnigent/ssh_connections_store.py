"""SSH connection profiles stored in ``~/.omnigent/config.yaml``."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH

_CONFIG_KEY = "ssh_connections"
_ALIAS_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")


@dataclass(frozen=True)
class SshConnectionProfile:
    """One SSH config Host alias the host daemon may use."""

    id: str
    label: str
    alias: str
    created_at: str
    codex_remote: bool = True
    cursor_remote: bool = True


def validate_ssh_alias(alias: str) -> str | None:
    """Return an error message when *alias* is invalid."""
    trimmed = alias.strip()
    if not trimmed:
        return "SSH alias is required"
    if not _ALIAS_RE.match(trimmed):
        return "SSH alias contains invalid characters"
    return None


def _parse_profile(raw: object) -> SshConnectionProfile | None:
    if not isinstance(raw, dict):
        return None
    profile_id = raw.get("id")
    label = raw.get("label")
    alias = raw.get("alias")
    created_at = raw.get("created_at")
    if not all(isinstance(value, str) for value in (profile_id, label, alias, created_at)):
        return None
    if validate_ssh_alias(alias) is not None:
        return None
    codex_remote = raw.get("codex_remote", True)
    if not isinstance(codex_remote, bool):
        codex_remote = True
    cursor_remote = raw.get("cursor_remote", True)
    if not isinstance(cursor_remote, bool):
        cursor_remote = True
    return SshConnectionProfile(
        id=profile_id,
        label=label.strip(),
        alias=alias.strip(),
        created_at=created_at,
        codex_remote=codex_remote,
        cursor_remote=cursor_remote,
    )


def read_ssh_connections(config_path: Path = CONFIG_PATH) -> list[SshConnectionProfile]:
    """Load SSH connection profiles from config.yaml."""
    if not config_path.exists():
        return []
    try:
        with config_path.open(encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
    except OSError:
        return []
    if not isinstance(cfg, dict):
        return []
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
    cfg: dict[str, object] = {}
    if config_path.exists():
        try:
            with config_path.open(encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle) or {}
            if isinstance(loaded, dict):
                cfg = loaded
        except OSError:
            cfg = {}
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
        "codex_remote": profile.codex_remote,
    }
