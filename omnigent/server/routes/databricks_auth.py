"""Databricks connection status and login flow.

Exposes three endpoints:

* ``GET /v1/databricks/status`` — checks whether the configured Databricks
  profile can resolve a valid workspace host + token.  Results are cached
  for 15 s so the sidebar poll does not spawn a CLI process on every tick.
  Returns ``host`` even when the token is expired (read from
  ``~/.databrickscfg``) so the UI can skip the URL input for re-login.
* ``POST /v1/databricks/login`` — starts ``databricks auth login`` with
  ``BROWSER=echo`` so the OAuth URL is captured from stdout and returned
  immediately.  When ``host`` is provided, passes ``--host <url>``;
  otherwise the CLI reads the host from ``~/.databrickscfg``.  The
  subprocess stays alive in the background waiting for the OAuth callback.
* ``GET /v1/databricks/login/poll`` — checks whether the login subprocess
  has exited.  On success, persists the Databricks profile to
  ``$OMNIGENT_DATA_DIR/config.yaml`` (``auth`` and ``llm`` blocks) so the
  server picks it up on the next status check.
"""

from __future__ import annotations

import asyncio
import configparser
import os
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from omnigent.runtime.credentials.databricks import resolve_databricks_workspace
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user

# Default profile name when none is configured yet.
_DEFAULT_PROFILE = "my-databricks"

# Status cache TTL — avoid spawning a CLI process on every poll.
_STATUS_CACHE_TTL_S = 15.0
_status_cache: tuple[float, dict[str, Any]] | None = None

# Active login subprocess state (only one login at a time).
_login_proc: asyncio.subprocess.Process | None = None
_login_profile: str | None = None


class DatabricksLoginRequest(BaseModel):
    """Body of ``POST /v1/databricks/login``."""

    host: str | None = Field(default=None, max_length=500)


def _databricks_profile(config: dict[str, Any]) -> str | None:
    """Extract the Databricks profile from the server config dict."""
    llm = config.get("llm")
    if isinstance(llm, dict) and llm.get("profile"):
        return llm["profile"]
    auth = config.get("auth")
    if isinstance(auth, dict) and auth.get("type") == "databricks":
        profile = auth.get("profile")
        if isinstance(profile, str) and profile:
            return profile
    providers = config.get("providers")
    if isinstance(providers, dict):
        for provider in providers.values():
            if not isinstance(provider, dict) or provider.get("kind") != "databricks":
                continue
            profile = provider.get("profile")
            if isinstance(profile, str) and profile:
                return profile
    return None


def _read_host_from_databrickscfg(profile: str) -> str | None:
    """Read the ``host`` field for *profile* from ``~/.databrickscfg``.

    The host is stored in plain text regardless of whether the token is
    valid, so this works even when token resolution fails.
    """
    path = os.path.expanduser("~/.databrickscfg")
    if not os.path.isfile(path):
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except configparser.Error:
        return None
    if parser.has_section(profile) and parser.has_option(profile, "host"):
        return parser.get(profile, "host") or None
    return None


def _check_databricks(profile: str | None) -> dict[str, Any]:
    """Try resolving the Databricks workspace; return status dict.

    Even when token resolution fails, the host is returned (read from
    ``~/.databrickscfg``) so the UI can skip the URL input for re-login.
    """
    if profile is None:
        return {
            "connected": False,
            "profile": None,
            "host": None,
            "error": None,
        }
    try:
        creds = resolve_databricks_workspace(profile)
        return {
            "connected": True,
            "profile": profile,
            "host": creds.host,
            "error": None,
        }
    except (OSError, ValueError) as exc:
        # Token resolution failed, but the host may still be in .databrickscfg.
        host = _read_host_from_databrickscfg(profile)
        return {
            "connected": False,
            "profile": profile,
            "host": host,
            "error": str(exc),
        }


def _persist_profile_to_config(profile: str, config: dict[str, Any]) -> None:
    """Write auth + llm profile to config.yaml if not already present.

    Uses deep-merge so existing keys in the auth/llm blocks are preserved.
    """
    from omnigent.cli_config import _save_global_config

    _save_global_config(
        {
            "auth": {"type": "databricks", "profile": profile},
            "llm": {"profile": profile},
        },
        deep_merge_keys=("auth", "llm"),
    )
    # Update the in-memory config so subsequent status checks see the profile
    # without requiring a server restart.
    auth = config.setdefault("auth", {})
    if not isinstance(auth, dict):
        auth = {}
        config["auth"] = auth
    auth.setdefault("type", "databricks")
    auth.setdefault("profile", profile)
    llm = config.setdefault("llm", {})
    if not isinstance(llm, dict):
        llm = {}
        config["llm"] = llm
    llm.setdefault("profile", profile)


def create_databricks_auth_router(
    auth_provider: AuthProvider | None = None,
    server_config: dict[str, Any] | None = None,
) -> APIRouter:
    """Build Databricks connection-status and login routes."""
    router = APIRouter()
    config = server_config if server_config is not None else {}

    @router.get("/databricks/status")
    async def get_databricks_status(request: Request) -> dict[str, Any]:
        require_user(request, auth_provider)

        global _status_cache
        now = time.time()
        if _status_cache is not None and now - _status_cache[0] < _STATUS_CACHE_TTL_S:
            return _status_cache[1]

        profile = _databricks_profile(config)
        result = await asyncio.to_thread(_check_databricks, profile)
        _status_cache = (now, result)
        return result

    @router.post("/databricks/login")
    async def databricks_login(
        request: Request,
        body: DatabricksLoginRequest,
    ) -> dict[str, Any]:
        require_user(request, auth_provider)

        global _login_proc, _login_profile, _status_cache

        if _login_proc is not None and _login_proc.returncode is None:
            return {"auth_url": None, "error": "A login is already in progress."}

        profile = _databricks_profile(config) or _DEFAULT_PROFILE
        host = body.host.strip() if body.host else None

        # Build CLI args.  When host is provided, pass --host so the CLI
        # writes it to ~/.databrickscfg (important for first-time setup).
        # When omitted, the CLI reads the host from the existing profile.
        cli_args = ["databricks", "auth", "login", "--profile", profile]
        if host:
            cli_args.extend(["--host", host])

        # BROWSER=echo makes the CLI print the OAuth URL to stdout instead
        # of opening a browser.  The subprocess stays alive until the OAuth
        # callback arrives on localhost:8020.
        try:
            proc = await asyncio.create_subprocess_exec(
                *cli_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "BROWSER": "echo"},
            )
        except FileNotFoundError:
            return {
                "auth_url": None,
                "error": "Databricks CLI not found. Install it with `pip install databricks-sdk` or `brew install databricks`.",
            }
        _login_proc = proc
        _login_profile = profile
        _status_cache = None  # Invalidate so next check re-resolves.

        # Read stdout until we get the OAuth URL (first line starting with https://).
        auth_url: str | None = None
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if text.startswith("https://"):
                    auth_url = text
                    break
        except asyncio.TimeoutError:
            pass

        if auth_url is None:
            # Kill the process and check stderr for a useful error.
            proc.kill()
            await proc.wait()
            stderr_bytes = await proc.stderr.read() if proc.stderr else b""
            stderr_text = stderr_bytes.decode(errors="replace").strip()
            _login_proc = None
            return {
                "auth_url": None,
                "error": stderr_text or "Could not capture OAuth URL from databricks CLI.",
            }

        return {"auth_url": auth_url, "profile": profile}

    @router.get("/databricks/login/poll")
    async def databricks_login_poll(request: Request) -> dict[str, Any]:
        require_user(request, auth_provider)

        global _login_proc, _login_profile, _status_cache

        if _login_proc is None:
            return {"completed": True, "success": False, "error": "No login in progress."}

        if _login_proc.returncode is None:
            return {"completed": False}

        # Process has exited.
        proc = _login_proc
        profile = _login_profile
        _login_proc = None
        _login_profile = None
        _status_cache = None

        if proc.returncode == 0:
            # Persist profile to config.yaml if it wasn't there before.
            if _databricks_profile(config) is None and profile is not None:
                _persist_profile_to_config(profile, config)
            return {"completed": True, "success": True, "profile": profile}

        stderr_bytes = await proc.stderr.read() if proc.stderr else b""
        stderr_text = stderr_bytes.decode(errors="replace").strip()
        return {
            "completed": True,
            "success": False,
            "error": stderr_text or f"databricks auth login exited with code {proc.returncode}",
        }

    return router
