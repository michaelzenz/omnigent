"""Singleton-plugin gating: run a plugin only on the host pinned to its bound role.

A plugin declares ``singleton: true`` + ``bound_role: <role>`` in its
``config.yaml``. The host reads the role's pinned ``host_id`` from the server
(via ``GET /v1/agent-tasks/roles/{role}/profile``) and runs the plugin only when
that ``host_id`` equals its own. The pin is sticky and user-controlled — never
auto-reassigned — so the plugin and the role share fate: if the pinned host is
down, nobody runs the plugin until the user reassigns the role.

The role ``host_id`` is cached briefly (default 60s) so a busy poller does not
hit the server every tick. On any fetch failure the plugin is skipped (safe —
no duplicate runs across hosts; the gap surfaces via healthcheck/observability
later).

NOTE — this host-side singleton gate is a temporary solution. Plugins still
live on the host and the host decides whether to run them. The durable answer
is to support creating custom plugins on the server (so a plugin's lifecycle,
scheduling, and state live server-side and don't depend on which host is
connected). We should design that server-side plugin model later and retire
this host-side gating.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

_logger = logging.getLogger(__name__)

_DEFAULT_ROLE_HOST_TTL_S = 60.0


class SingletonConfigError(ValueError):
    """Raised when a plugin's singleton config is missing or invalid.

    The poller catches this, logs a warning, and skips the plugin (it does not
    run that tick). Surfacing the failure to the user is a later concern
    (server-side plugin monitoring).
    """


@dataclass(frozen=True)
class SingletonConfig:
    """Singleton scheduling fields parsed from a plugin's ``config.yaml``."""

    singleton: bool = False
    bound_role: str | None = None


def parse_singleton_config(cfg: object) -> SingletonConfig:
    """Parse ``singleton`` / ``bound_role`` from a plugin config dict.

    ``singleton`` is REQUIRED (every plugin must explicitly declare it true or
    false so a forgotten field never silently causes duplicate polling across
    hosts). ``bound_role`` is required when ``singleton: true``. On any
    violation :class:`SingletonConfigError` is raised; the poller catches it
    and skips the plugin.
    """
    if not isinstance(cfg, dict):
        raise SingletonConfigError(
            "plugin config is not a dict; singleton: true|false is required"
        )
    if "singleton" not in cfg:
        raise SingletonConfigError(
            "config.yaml must declare `singleton: true|false`. Set "
            "`singleton: true` + `bound_role: <role>` to run on one host, or "
            "`singleton: false` to run on every host."
        )
    singleton = bool(cfg.get("singleton"))
    bound_role = cfg.get("bound_role")
    if not isinstance(bound_role, str):
        bound_role = None
    if singleton and not (bound_role and bound_role.strip()):
        raise SingletonConfigError(
            "singleton: true requires `bound_role: <role>` (e.g. "
            "`bound_role: secretary`). Set singleton: false if the plugin "
            "should run on every host."
        )
    return SingletonConfig(singleton=singleton, bound_role=bound_role or None)


class RoleHostResolver:
    """Caching resolver for the pinned ``host_id`` of a role.

    One instance is meant to live on a poller for its lifetime; the cache is
    in-memory and per-process, so a fresh host process starts cold.
    """

    def __init__(
        self, client: httpx.AsyncClient, *, ttl_s: float = _DEFAULT_ROLE_HOST_TTL_S
    ) -> None:
        self._client = client
        self._ttl_s = ttl_s
        self._cache: dict[str, tuple[str | None, float]] = {}

    async def get_role_host_id(self, role: str) -> str | None:
        """Return the cached-or-fresh ``host_id`` for *role*, or ``None``.

        ``None`` means the role has no pinned host yet, or the fetch failed;
        callers treat ``None`` as "do not run" so a missing pin never causes
        duplicate runs across hosts.
        """
        now = time.monotonic()
        cached = self._cache.get(role)
        if cached and now - cached[1] < self._ttl_s:
            return cached[0]
        host_id = await self._fetch_role_host_id(role)
        self._cache[role] = (host_id, now)
        return host_id

    async def _fetch_role_host_id(self, role: str) -> str | None:
        path = f"/v1/agent-tasks/roles/{role}/profile"
        try:
            resp = await self._client.get(path)
        except Exception:  # noqa: BLE001 -- network/auth failure → safe skip
            _logger.warning(
                "singleton: failed to fetch role profile %s (plugin will be skipped): %s",
                role,
                path,
                exc_info=True,
            )
            return None
        if resp.status_code != 200:
            _logger.warning(
                "singleton: GET %s returned HTTP %s — plugin will be skipped",
                path,
                resp.status_code,
            )
            return None
        try:
            data = resp.json()
        except ValueError:
            _logger.warning("singleton: non-JSON role profile response for %s", role)
            return None
        host_id = data.get("host_id")
        if not isinstance(host_id, str):
            return None
        return host_id


async def should_run_singleton(
    resolver: RoleHostResolver,
    cfg: SingletonConfig,
    *,
    host_id: str,
) -> bool:
    """Return whether *this* host should run a singleton plugin this tick.

    Non-singleton plugins always return ``True``. Singleton plugins return
    ``True`` only when the bound role's pinned ``host_id`` equals *host_id*.
    On fetch failure or missing pin, returns ``False`` (safe skip).
    """
    if not cfg.singleton or not cfg.bound_role:
        return True
    role_host_id = await resolver.get_role_host_id(cfg.bound_role)
    if role_host_id is None:
        return False
    return role_host_id == host_id
