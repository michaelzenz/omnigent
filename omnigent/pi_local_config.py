"""Detect a usable self-contained Pi model configuration on this host."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

HARNESS_PI_LOCAL_CONFIG_DIR_ENV = "HARNESS_PI_LOCAL_CONFIG_DIR"
HARNESS_PI_LOCAL_PROVIDER_IDS_ENV = "HARNESS_PI_LOCAL_PROVIDER_IDS"


@dataclass(frozen=True)
class PiLocalConfig:
    """A Pi agent directory and the custom providers that can authenticate."""

    agent_dir: Path
    provider_ids: tuple[str, ...]


def _agent_dir() -> Path:
    configured = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".pi" / "agent"


def _self_contained_providers(models: object) -> tuple[str, ...]:
    if not isinstance(models, dict):
        return ()
    providers = models.get("providers")
    if not isinstance(providers, dict):
        return ()
    result: list[str] = []
    for provider_id, raw in providers.items():
        if not isinstance(provider_id, str) or not isinstance(raw, dict):
            continue
        # ucode writes a provider-local apiKey command into models.json. Limit
        # adoption to that self-contained shape so Onih never guesses which
        # ambient credential or auth.json entry a provider intended to use.
        api_key = raw.get("apiKey")
        models_raw = raw.get("models")
        if isinstance(api_key, str) and api_key.strip() and isinstance(models_raw, list):
            if any(
                isinstance(model, dict) and isinstance(model.get("id"), str)
                for model in models_raw
            ):
                result.append(provider_id)
    return tuple(result)


def resolve_usable_pi_local_config(
    *,
    pi_path: str | None = None,
    timeout_seconds: float = 5.0,
) -> PiLocalConfig | None:
    """Return ucode-style local Pi config when Pi confirms usable auth.

    The probe never asks Pi to print credentials. ``--no-refresh`` keeps the
    readiness check bounded and avoids mutating OAuth state.
    """

    agent_dir = _agent_dir()
    try:
        payload = json.loads((agent_dir / "models.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    candidates = _self_contained_providers(payload)
    if not candidates:
        return None

    executable = pi_path or os.environ.get("OMNIGENT_PI_PATH", "").strip() or shutil.which("pi")
    if not executable:
        return None
    probe_env = os.environ.copy()
    probe_env["PI_CODING_AGENT_DIR"] = str(agent_dir)
    ready: list[str] = []
    for provider_id in candidates:
        try:
            completed = subprocess.run(
                [
                    executable,
                    "auth",
                    "check",
                    "--provider",
                    provider_id,
                    "--json",
                    "--no-refresh",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=probe_env,
            )
            result = json.loads(completed.stdout) if completed.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            continue
        if isinstance(result, dict) and result.get("status") == "ready":
            ready.append(provider_id)
    if not ready:
        return None
    return PiLocalConfig(agent_dir=agent_dir, provider_ids=tuple(ready))
