"""
Server-side proxies for the host git-worktree tunnel frames.

Enqueue a ``host.create_worktree`` / ``host.remove_worktree`` frame,
register a future on the host connection, and await the result. The host
(not the server) runs git. See designs/SESSION_GIT_WORKTREE.md.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from omnigent.host.frames import (
    HostCreateWorktreeFrame,
    HostListWorktreesFrame,
    HostRemoveWorktreeFrame,
    HostRenewWorktreeLeaseFrame,
    HostWorktreeSizesFrame,
    encode_host_frame,
)
from omnigent.server.host_registry import HostConnection, HostRegistry

_logger = logging.getLogger(__name__)

# Worktree operations may materialize very large repositories. Production
# waits for the host result without a deadline; tests may inject one.
_WORKTREE_TIMEOUT_S: float | None = None


class WorktreeProxyError(Exception):
    """
    Raised when the host reports a worktree operation failure.

    These are typically user-correctable input problems (branch
    already exists, not a git repo, bad base ref), so the route layer
    maps this to ``INVALID_INPUT`` (400).

    :param message: Human-readable error suitable for the API
        response body, e.g.
        ``"worktree creation failed: branch already exists"``.
    """

    def __init__(self, message: str) -> None:
        """
        Initialize with the user-facing error message.

        :param message: Error string surfaced to the API caller.
        """
        super().__init__(message)
        self.message = message


class WorktreeHostUnavailableError(WorktreeProxyError):
    """
    Raised when the host can't be reached for a worktree operation.

    Connection loss is an infrastructure condition, not user input. The route layer maps this to
    ``CONFLICT`` (409). Subclasses :class:`WorktreeProxyError` so
    best-effort callers that catch the base type still catch it.
    """


@dataclass
class CreatedWorktree:
    """
    Result of a successful host worktree creation.

    :param worktree_path: Absolute path of the created worktree
        directory on the host, e.g.
        ``"/Users/alice/myrepo-worktrees/feature-login"``. Stored as
        the session ``workspace``.
    :param branch: The branch checked out in the worktree, e.g.
        ``"feature/login"``.
    """

    worktree_path: str
    branch: str


async def _await_host_worktree_result(
    *,
    host_registry: HostRegistry,
    host_conn: HostConnection,
    pending: dict[str, asyncio.Future[dict[str, object]]],
    request_id: str,
    frame: str,
    op: str,
    timeout_s: float | None = None,
) -> dict[str, object]:
    """
    Send a worktree frame and await its matching result over the tunnel.

    Shared plumbing for the create/remove proxies: register a future on
    ``pending`` keyed by ``request_id``, enqueue ``frame``, await the
    reply, and clean up on every path.

    :param host_registry: Registry used to enqueue the outbound frame.
    :param host_conn: Live host connection.
    :param pending: The connection's pending-future map for this op
        (``pending_create_worktrees`` or ``pending_remove_worktrees``).
    :param request_id: Correlation id already embedded in ``frame``.
    :param frame: Encoded host frame to send.
    :param op: Short label for error messages, e.g.
        ``"worktree creation"``.
    :returns: The host's result dict (``status`` plus op-specific
        fields).
    :raises WorktreeHostUnavailableError: On connection loss.
    """
    future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    pending[request_id] = future
    effective_timeout = _WORKTREE_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        try:
            host_registry.send_text(host_conn, frame)
        except ConnectionError as exc:
            raise WorktreeHostUnavailableError(
                f"host '{host_conn.host_id}' connection lost during {op}"
            ) from exc
        try:
            if effective_timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=effective_timeout)
        except asyncio.TimeoutError as exc:
            raise WorktreeHostUnavailableError(
                f"host '{host_conn.host_id}' did not respond to {op} within "
                f"{effective_timeout:.0f}s"
            ) from exc
        except ConnectionError as exc:
            raise WorktreeHostUnavailableError(
                f"host '{host_conn.host_id}' connection lost during {op}"
            ) from exc
    finally:
        pending.pop(request_id, None)


async def create_worktree_on_host(
    *,
    host_registry: HostRegistry,
    host_conn: HostConnection,
    repo_path: str,
    branch_name: str,
    base_branch: str | None,
    auto_fetch_base: bool = False,
    on_log: Callable[[str], None] | None = None,
    auto_reuse: bool = False,
    reuse_existing_branch: bool = False,
    lease_owner: str | None = None,
    lease_seconds: int = 86_400,
) -> CreatedWorktree:
    """Send a ``host.create_worktree`` frame and await the result.

    When ``on_log`` is supplied, each streamed git output line the host
    emits before the final result is relayed to the callback, so the
    caller can publish it to the session's SSE stream in real time.

    :param host_registry: Server-side registry; used to enqueue the
        outbound frame on the host's send queue.
    :param host_conn: Live host connection to create the worktree on.
    :param repo_path: Absolute path inside the source repo on the
        host.
    :param branch_name: New branch to create, e.g. ``"feature/login"``.
    :param base_branch: Optional base ref, e.g. ``"main"``. ``None``
        branches from the repo's current ``HEAD``.
    :param auto_fetch_base: Whether the host may fetch and retry a missing base.
    :param on_log: Optional callback for each streamed git output line.
    :returns: The created worktree's path and branch.
    :raises WorktreeHostUnavailableError: If the host connection drops.
    :raises WorktreeProxyError: If the host reports a worktree failure.
    """
    request_id = secrets.token_hex(8)
    if on_log is not None:
        host_conn.pending_worktree_log_handlers[request_id] = on_log
    try:
        frame = encode_host_frame(
            HostCreateWorktreeFrame(
                request_id=request_id,
                repo_path=repo_path,
                branch_name=branch_name,
                base_branch=base_branch,
                auto_fetch_base=auto_fetch_base,
                auto_reuse=auto_reuse,
                reuse_existing_branch=reuse_existing_branch,
                lease_owner=lease_owner,
                lease_seconds=lease_seconds,
            )
        )
        result = await _await_host_worktree_result(
            host_registry=host_registry,
            host_conn=host_conn,
            pending=host_conn.pending_create_worktrees,
            request_id=request_id,
            frame=frame,
            op="worktree creation",
        )
    finally:
        host_conn.pending_worktree_log_handlers.pop(request_id, None)
    if result.get("status") != "ok":
        raise WorktreeProxyError(
            f"worktree creation failed: {result.get('error') or 'host reported no detail'}"
        )
    worktree_path = result.get("worktree_path")
    branch = result.get("branch")
    if not isinstance(worktree_path, str) or not isinstance(branch, str):
        raise WorktreeProxyError("host returned an incomplete worktree result")
    return CreatedWorktree(worktree_path=worktree_path, branch=branch)


async def remove_worktree_on_host(
    *,
    host_registry: HostRegistry,
    host_conn: HostConnection,
    worktree_path: str,
    branch: str | None,
    delete_branch: bool,
) -> None:
    """
    Send a ``host.remove_worktree`` frame and await the result.

    :param host_registry: Server-side registry; used to enqueue the
        outbound frame on the host's send queue.
    :param host_conn: Live host connection that owns the worktree.
    :param worktree_path: Absolute path of the worktree to remove on
        the host, e.g. ``"/Users/alice/myrepo-worktrees/feature-login"``.
    :param branch: Branch to delete when ``delete_branch`` is
        ``True``, e.g. ``"feature/login"``. ``None`` skips branch
        deletion.
    :param delete_branch: When ``True``, delete ``branch`` after
        removing the worktree directory.
    :raises WorktreeHostUnavailableError: If the host connection drops.
    :raises WorktreeProxyError: If the host reports a removal failure.
    """
    request_id = secrets.token_hex(8)
    frame = encode_host_frame(
        HostRemoveWorktreeFrame(
            request_id=request_id,
            worktree_path=worktree_path,
            branch=branch,
            delete_branch=delete_branch,
        )
    )
    result = await _await_host_worktree_result(
        host_registry=host_registry,
        host_conn=host_conn,
        pending=host_conn.pending_remove_worktrees,
        request_id=request_id,
        frame=frame,
        op="worktree removal",
    )
    if result.get("status") != "ok":
        raise WorktreeProxyError(
            f"worktree removal failed: {result.get('error') or 'host reported no detail'}"
        )


async def list_worktrees_on_host(
    *,
    host_registry: HostRegistry,
    host_conn: HostConnection,
    repo_path: str,
) -> list[dict[str, object]]:
    """
    Send a ``host.list_worktrees`` frame and await the result.

    :param host_registry: Server-side registry; used to enqueue the
        outbound frame on the host's send queue.
    :param host_conn: Live host connection to list worktrees on.
    :param repo_path: Absolute path inside the source repo on the
        host — the canonical picked directory, e.g.
        ``"/Users/alice/myrepo"``.
    :returns: One dict per worktree with keys ``path``, ``branch``,
        ``is_main``, ``detached`` (main first).
    :raises WorktreeHostUnavailableError: If the host connection drops.
    :raises WorktreeProxyError: If the host reports a listing failure.
    """
    request_id = secrets.token_hex(8)
    frame = encode_host_frame(
        HostListWorktreesFrame(
            request_id=request_id,
            repo_path=repo_path,
        )
    )
    result = await _await_host_worktree_result(
        host_registry=host_registry,
        host_conn=host_conn,
        pending=host_conn.pending_list_worktrees,
        request_id=request_id,
        frame=frame,
        op="worktree listing",
    )
    if result.get("status") != "ok":
        raise WorktreeProxyError(
            f"worktree listing failed: {result.get('error') or 'host reported no detail'}"
        )
    worktrees = result.get("worktrees")
    if not isinstance(worktrees, list):
        raise WorktreeProxyError("host returned an incomplete worktree list")
    return worktrees


async def renew_worktree_lease_on_host(
    *,
    host_registry: HostRegistry,
    host_conn: HostConnection,
    worktree_path: str,
    lease_owner: str,
    lease_seconds: int = 86_400,
    release: bool = False,
) -> bool:
    """Renew a managed worktree lease when ``lease_owner`` still owns it."""
    request_id = secrets.token_hex(8)
    frame = encode_host_frame(
        HostRenewWorktreeLeaseFrame(
            request_id=request_id,
            worktree_path=worktree_path,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            release=release,
        )
    )
    result = await _await_host_worktree_result(
        host_registry=host_registry,
        host_conn=host_conn,
        pending=host_conn.pending_renew_worktree_leases,
        request_id=request_id,
        frame=frame,
        op="worktree lease renewal",
        timeout_s=10.0,
    )
    if result.get("status") != "ok":
        raise WorktreeProxyError(
            f"worktree lease renewal failed: {result.get('error') or 'host reported no detail'}"
        )
    return result.get("renewed") is True


async def release_worktree_lease_on_host(
    *,
    host_registry: HostRegistry,
    host_conn: HostConnection,
    worktree_path: str,
    lease_owner: str,
) -> bool:
    """Release a managed lease after explicit session deletion."""
    return await renew_worktree_lease_on_host(
        host_registry=host_registry,
        host_conn=host_conn,
        worktree_path=worktree_path,
        lease_owner=lease_owner,
        release=True,
    )


# Timeout for the worktree-sizes round-trip. The host's per-worktree du cap is
# 300s; add headroom for git worktree list + tunnel round-trip.
_WORKTREE_SIZES_TIMEOUT_S = 310.0


async def worktree_sizes_on_host(
    *,
    host_registry: HostRegistry,
    host_conn: HostConnection,
    repo_path: str,
    force: bool = False,
) -> dict[str, object]:
    """Send a ``host.worktree_sizes`` frame and await the result.

    :param force: When True, host recalculates immediately (refresh button).
    :returns: Dict with status, worktrees, total_bytes, calculated_at, error.
    :raises WorktreeHostUnavailableError: If the host connection drops.
    :raises WorktreeProxyError: If the host reports a failure.
    """
    request_id = secrets.token_hex(8)
    frame = encode_host_frame(
        HostWorktreeSizesFrame(
            request_id=request_id,
            repo_path=repo_path,
            force=force,
        )
    )
    result = await _await_host_worktree_result(
        host_registry=host_registry,
        host_conn=host_conn,
        pending=host_conn.pending_worktree_sizes,
        request_id=request_id,
        frame=frame,
        op="worktree sizes",
        timeout_s=_WORKTREE_SIZES_TIMEOUT_S,
    )
    if result.get("status") != "ok" and result.get("status") != "failed":
        raise WorktreeProxyError(
            f"worktree sizes failed: {result.get('error') or 'host reported no detail'}"
        )
    return result
