"""Runner FastAPI app — spawns harness subprocesses and dispatches to them.

Per ``designs/RUNNER.md`` §1, the runner owns harness subprocesses.
It resolves the harness type + spawn-env from the agent spec (either
via a spec_resolver callback for in-process use, or via
GET /v1/agents/{id}/contents for out-of-process use).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

if TYPE_CHECKING:
    # Type-only import: the runner keeps codex deps out of its runtime import
    # graph (they are imported lazily inside the codex-native helpers).
    from omnigent.runner.subagent_routing import SubagentRouter
    from omnigent.runner.turn_routing import TurnRouter

import httpx
from fastapi.responses import JSONResponse

from omnigent.debug_logging import runner_primary_session_id
from omnigent.entities.session_resources import (
    SessionResourceView,
    session_resource_view_to_dict,
)
from omnigent.runner.session_init_protocol import (
    RunnerSessionInitEnvelope,
)

_LOG = logging.getLogger(__name__)


#: Root of the installed ``omnigent`` package, for locating packaged assets
#: (e.g. ``onboarding/agent/skills/``) independently of this module's depth.
_OMNIGENT_PACKAGE_DIR = Path(__file__).resolve().parent.parent.parent


class _EnsureCommentRelay(Protocol):
    """Callable contract for starting a session's native tool relay."""

    async def __call__(
        self,
        session_id: str,
        *,
        bridge_id: str | None = None,
        explicit_bridge_dir: Path | None = None,
        await_notify: bool = False,
        session_labels: Mapping[str, str] | None = None,
    ) -> None:
        pass


# Background transcript-forwarder tasks for host-spawned claude-native and
# codex-native runners, keyed by session id: strong references so they aren't
# garbage-collected mid-run, and the handle for cancelling a session's previous
# forwarder on terminal re-create (else both mirror, double-posting items).

# Bound how long terminal (re)creation waits for a cancelled forwarder.

# Delegated runner bearers last 30 minutes and refresh five minutes before
# expiry. A one-minute cadence allows several retries without giving the child
# the runner binding token; cached factory calls stay local and cheap.


# Background tasks that re-pop a still-pending cost-budget approval on a
# terminal client that attaches after the ASK fired. Kept referenced so
# they aren't garbage-collected before they run.

# Background Codex app-server instances for host-spawned codex-native
# runners, kept referenced so they aren't garbage-collected mid-run.

# Background OpenCode ``opencode serve`` instances for host-spawned
# opencode-native runners, kept referenced so they aren't garbage-collected
# mid-run (mirrors ``_AUTO_CODEX_APP_SERVERS``).

# Bound repeated terminal GET miss logs from tight client poll loops.


class _NativeRouterLaunch(NamedTuple):
    """What a native launch site needs back from the router start.

    :param advertised_dir: Directory to point the harness's hooks at, or
        ``None`` when no endpoint is running.
    :param router: The handle to hand back to
        :func:`_shutdown_session_router_async`, so a delayed teardown from
        this launch cannot close a router a re-create has since installed.
    """

    advertised_dir: Path | None
    router: SubagentRouter | None


def _start_subagent_router_for_native_session(
    session_id: str,
    *,
    bridge_dir: Path,
    harness: str,
    server_client: httpx.AsyncClient | None,
    routing_enabled: bool,
    auto_harness: bool,
) -> _NativeRouterLaunch:
    """Start the subagent-routing endpoint for a native session.

    Native harnesses enforce routing through hooks configured at terminal
    launch, so the endpoint has to be live (and advertised in the bridge
    dir the hooks read) before the CLI starts.

    Installed for Smart Routing sessions only, on both families: a plain
    session launches like a plain one, with no loopback server, no bearer
    token on disk and no spawn hook on its argv. On the codex family the
    advertisement additionally turns on a generated ``hooks.json`` and the
    routed-spawn tool pre-approvals — which is why a pinned Smart Routing
    codex session needs it too: without them its spawn tools are neither
    gated nor pre-approved, so the spawn stalls on an approval prompt
    nobody is watching. See ``ensure_session_router_quietly``.

    :param session_id: Session/conversation identifier.
    :param bridge_dir: Session bridge directory the hooks discover.
    :param harness: Harness the router is being installed for; logged on
        failure.
    :param server_client: Runner→server client the relay forwards on.
    :param routing_enabled: Whether the session launched with Smart Routing
        on. The gate on both families. Stamped at create, so a plain
        session stays plain even if the gear's subagent-routing toggle is
        flipped mid-session.
    :param auto_harness: Whether Smart Routing also owns this session's
        harness, so its spawns may cross families. Not required for the
        endpoint; it decides what the router may offer.
    :returns: The advertisement directory to point hooks at (``None`` when
        the endpoint could not start) paired with the router handle.
    """
    from omnigent.runner.subagent_routing import (
        SessionRoutingClass,
        ensure_session_router_quietly,
    )

    router = ensure_session_router_quietly(
        session_id,
        bridge_dir=bridge_dir,
        server_client=server_client,
        harness=harness,
        routing_class=SessionRoutingClass(
            routing_enabled=routing_enabled,
            auto_harness=auto_harness,
        ),
    )
    return _NativeRouterLaunch(bridge_dir if router is not None else None, router)


def _start_turn_router_for_native_session(
    session_id: str,
    *,
    bridge_dir: Path,
    harness: str,
    server_client: httpx.AsyncClient | None,
    turn_routing: bool,
) -> TurnRouter | None:
    """Start the first-message turn-routing endpoint for a native session.

    Installed only for a session whose first prompt still has to be routed.
    The advertisement it writes is also the switch the harness launch reads to
    decide whether to register the ``UserPromptSubmit`` routing hook at all,
    so a session that will never route pays no round trip — neither one with
    routing off, nor one a web create already routed before the pane launched.

    :param session_id: Session/conversation identifier.
    :param bridge_dir: Session bridge directory the hook discovers.
    :param harness: Harness the endpoint is being installed for.
    :param server_client: Runner→server client the relay forwards on, and
        the replay delivers through.
    :param turn_routing: Whether this session still needs first-message
        routing (:class:`~omnigent.runner.subagent_routing.SessionRoutingClass`).
    :returns: The router handle, or ``None`` when nothing is left to route
        for this session or the endpoint could not start.
    """
    from omnigent.runner.turn_routing import ensure_session_turn_router

    return ensure_session_turn_router(
        session_id,
        bridge_dir=bridge_dir,
        server_client=server_client,
        harness=harness,
        routing_enabled=turn_routing,
    )


def _recover_pending_turn_replay(
    session_id: str,
    *,
    bridge_dir: Path,
    server_client: httpx.AsyncClient | None,
) -> None:
    """Redeliver a routed prompt a previous launch blocked but never replayed.

    Fire-and-forget and best effort: no pending record (the normal case) is
    a no-op, and a recovery that cannot run must never fail a launch.

    :param session_id: Session/conversation identifier.
    :param bridge_dir: Session bridge directory holding the pending record.
    :param server_client: Runner→server client the prompt is delivered on.
    :returns: None.
    """
    from omnigent.runner.turn_routing import schedule_pending_replay_recovery

    try:
        schedule_pending_replay_recovery(
            session_id,
            bridge_dir=bridge_dir,
            server_client=server_client,
        )
    except Exception:  # noqa: BLE001 - a recovery must not take the launch down
        _LOG.warning(
            "turn-routing replay recovery could not start for session=%s",
            session_id,
            exc_info=True,
            extra={"session_id": session_id},
        )


async def _shutdown_session_turn_router_async(
    session_id: str, router: TurnRouter | None = None
) -> None:
    """Tear down a session's turn-routing endpoint off the event loop.

    :param session_id: Session/conversation identifier.
    :param router: Handle this launch started, so a late teardown cannot
        close the endpoint a re-created terminal has since installed.
    :returns: None.
    """
    from omnigent.runner.turn_routing import shutdown_session_turn_router

    await asyncio.to_thread(shutdown_session_turn_router, session_id, router)


async def _shutdown_session_router_async(
    session_id: str, router: SubagentRouter | None = None
) -> None:
    """Tear down a session's subagent-routing endpoint off the event loop.

    ``shutdown_session_router`` joins the router's serving thread, so
    calling it inline would block the loop for up to the shutdown poll
    interval. A session with no router is a no-op.

    :param session_id: Session/conversation identifier.
    :param router: Handle this launch started. Passing it scopes the
        teardown to that router, so a forwarder whose ``finally`` runs
        after a terminal re-create does not close the new session's live
        endpoint.
    :returns: None.
    """
    from omnigent.runner.subagent_routing import shutdown_session_router

    await asyncio.to_thread(shutdown_session_router, session_id, router)


# Permission decisions can park a human approval card server-side
# (``POLICY_ACTION_ASK``), so the evaluate POST may block until a human
# resolves it. Match the codex-native policy hook's day-long budget; the
# server caps the real wait via the deciding policy's ``ask_timeout``.
# Map the server's proto verdict onto the forwarder's verdict vocabulary
# (``map_verdict_to_decision`` reads ``decision``). Anything unknown is
# treated as ``ask`` → the forwarder fails it closed to ``reject``.


async def _post_pi_native_credential_warning(
    *,
    session_id: str,
    server_client: httpx.AsyncClient | None,
    warning: str,
) -> None:
    """Surface a credential warning into the session as an error banner.

    Posts an ``error`` item via ``external_conversation_item`` so the notice
    renders as the web UI's distinct destructive banner (not a misleading
    assistant bubble), persists across reload, and — because ``error`` is a
    non-content item type — never enters the next turn's model context. The
    event persists without queuing an agent turn, so it's safe on a session
    whose model is unreachable.

    :param session_id: Session/conversation identifier.
    :param server_client: Runner Omnigent server client (``None`` in tests).
    :param warning: The user-facing warning text to surface.
    """
    if server_client is None:
        return
    try:
        resp = await server_client.post(
            f"/v1/sessions/{urllib.parse.quote(session_id, safe='')}/events",
            json={
                "type": "external_conversation_item",
                "data": {
                    "item_type": "error",
                    "item_data": {
                        "source": "execution",
                        "code": "pi_credentials_unresolved",
                        "message": warning,
                    },
                },
            },
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        _LOG.warning(
            "pi-native: failed to surface credential warning for session %s",
            session_id,
            exc_info=True,
        )


# Cold-start port-discovery budget. agy's connect-RPC server binds its loopback
# port a moment AFTER the process starts (per-process, BEFORE any conversation
# exists), so the bootstrap polls rather than probing once. The total wait is
# bounded so a never-binding agy cannot hang the launch; the reader still spawns
# afterward and keeps polling discovery as a functional fallback.
# A non-empty model catalog is the first reliable signal that agy post-login
# initialization has reached the model service. Live cold starts still needed a
# short settling window after that response before StartCascade was reliable.
_AGY_COLD_START_MODEL_STABILIZATION_S = 4.0


# How long to wait for agy to write a cold-started conversation into this
# session's Gemini dir before judging it foreign. agy creates the db as part of
# ``StartCascade`` (observed same-second), so this only absorbs filesystem lag.
_AGY_CASCADE_OWNERSHIP_GRACE_S = 3.0
_AGY_CASCADE_OWNERSHIP_POLL_S = 0.25


#: Transcript role labels for the fork preamble. cursor's TUI can't reconstruct
#: native user/assistant bubbles (its conversation is server-backed), so the
#: replayed history reads as close to that as a single text block allows:
#: capitalized speaker labels, blank-line-separated turns.


def _measured_prefix_bytes(transcript_path: Path) -> int | None:
    """
    Measure a just-written resume transcript so the forwarder can skip exactly it.

    Taken before Claude launches, while the file holds only the synthesized
    prefix. ``None`` on any read failure, which leaves the forwarder on its
    live end-offset fallback.

    :param transcript_path: Resume transcript this launch wrote, e.g.
        ``Path("~/.claude/projects/-Users-me-repo/<sid>.jsonl")``.
    :returns: File size in bytes, or ``None`` when it cannot be measured.
    """
    try:
        return transcript_path.stat().st_size
    except OSError:
        _LOG.warning(
            "Could not measure synthesized Claude resume transcript; "
            "forwarder will seed from the live transcript end; transcript=%s",
            transcript_path,
            exc_info=True,
            extra={"session_id": runner_primary_session_id()},
        )
        return None


#: Omnigent MCP tools an auto-harness Claude session must be able to call
#: without an interactive prompt: the two the cross-harness redirect names, the
#: one that delivers the sub-task, and the one that collects its result. The
#: native path passes no allowlist otherwise, so Claude Code's "don't ask mode"
#: denies them outright ("Permission to use mcp__omnigent__sys_read_inbox has
#: been denied"). Narrower than the SDK arm, which pre-approves every Omnigent
#: tool in ``auto`` / ``bypassPermissions``.
_ROUTED_SPAWN_ALLOWED_TOOLS: tuple[str, ...] = (
    "mcp__omnigent__sys_session_create",
    "mcp__omnigent__sys_agent_list",
    "mcp__omnigent__sys_session_send",
    "mcp__omnigent__sys_read_inbox",
)


def _routed_spawn_launch_args(
    auto_harness: bool, *, router_started: bool = True
) -> tuple[str | None, tuple[str, ...]]:
    """
    Resolve the routed-spawn additions to a Claude terminal's argv.

    :param auto_harness: ``True`` for a session whose spawns the router may
        move across harness families.
    :param router_started: ``False`` when the spawn router did not come up, so
        nothing would honour the note or need the pre-approvals. Instructing
        Claude to hand its spawns to a router that is not there would only
        make it argue with a hook that never answers.
    :returns: ``(append_system_prompt, allowed_tools)`` for
        :func:`augment_claude_args`. ``(None, ())`` leaves the argv exactly as
        a pinned session's, which is the point of the gate.
    """
    if not auto_harness or not router_started:
        return None, ()
    from omnigent.inner.hook_scripts.subagent_router import smart_routing_spawn_note

    return smart_routing_spawn_note("claude-native"), _ROUTED_SPAWN_ALLOWED_TOOLS


@dataclasses.dataclass(frozen=True)
class _ClaudeSessionLaunchMetadata:
    """Persisted values consumed by Claude terminal launch."""

    reasoning_effort: str | None = None
    model_override: str | None = None
    terminal_launch_args: list[str] | None = None
    external_session_id: str | None = None
    fork_source_external_id: str | None = None
    fork_carry_history: bool = False
    #: Both routing fields come from ``routing_class_from_snapshot``, so an
    #: auto-harness session always reads as routing-enabled too. Deriving them
    #: separately was the bug: a sub-agent child of a routed parent carries the
    #: auto-harness label but no ``cost_control_mode_override``, and it launched
    #: with the routed-spawn note and tool pre-approvals but no router, no
    #: pinned arms and no launch-model pin.
    routing_enabled: bool = False
    #: Session started in Smart Routing's auto-harness mode, so the router may
    #: place its subagents on the counterpart harness family. Only these
    #: sessions get the routed-spawn system-prompt note and tool pre-approval;
    #: a pinned session's argv stays byte-identical.
    auto_harness: bool = False
    #: The first-message routing hook still has work to do. False once the
    #: session carries a routing decision — a web create routes the model
    #: before the pane launches — so no prompt is ever held and replayed.
    turn_routing: bool = False


def _claude_launch_metadata_from_envelope(
    session_init: RunnerSessionInitEnvelope,
) -> _ClaudeSessionLaunchMetadata:
    """Project Claude launch metadata without server callbacks."""
    from omnigent.runner.subagent_routing import routing_class_from_snapshot
    from omnigent.stores.conversation_store import (
        FORK_CARRY_HISTORY_LABEL_KEY,
        FORK_SOURCE_EXTERNAL_SESSION_LABEL_KEY,
    )

    snapshot = session_init.snapshot
    fork_source = snapshot.labels.get(FORK_SOURCE_EXTERNAL_SESSION_LABEL_KEY)
    routing_class = routing_class_from_snapshot(
        cost_control_mode=snapshot.cost_control_mode_override,
        harness_override=snapshot.harness_override,
        labels=snapshot.labels,
    )
    return _ClaudeSessionLaunchMetadata(
        routing_enabled=routing_class.routing_enabled,
        auto_harness=routing_class.auto_harness,
        turn_routing=routing_class.turn_routing,
        reasoning_effort=snapshot.reasoning_effort,
        model_override=snapshot.model_override,
        terminal_launch_args=snapshot.terminal_launch_args,
        external_session_id=snapshot.external_session_id,
        fork_source_external_id=(
            fork_source if isinstance(fork_source, str) and fork_source else None
        ),
        fork_carry_history=snapshot.labels.get(FORK_CARRY_HISTORY_LABEL_KEY) == "1",
    )


async def _load_legacy_claude_launch_metadata(
    server_client: httpx.AsyncClient,
    session_id: str,
) -> _ClaudeSessionLaunchMetadata:
    """Fetch Claude launch metadata for servers predating the init envelope."""
    from omnigent.runner.subagent_routing import routing_class_from_snapshot
    from omnigent.stores.conversation_store import (
        FORK_CARRY_HISTORY_LABEL_KEY,
        FORK_SOURCE_EXTERNAL_SESSION_LABEL_KEY,
    )

    try:
        response = await server_client.get(
            f"/v1/sessions/{urllib.parse.quote(session_id, safe='')}",
            timeout=10.0,
        )
    except httpx.HTTPError:
        _LOG.debug(
            "Could not fetch session launch config for %s; terminal will use Claude's defaults",
            session_id,
        )
        return _ClaudeSessionLaunchMetadata()
    if response.status_code != 200:
        return _ClaudeSessionLaunchMetadata()

    snapshot = response.json()
    effort = snapshot.get("reasoning_effort")
    model_override = snapshot.get("model_override")
    launch_args = snapshot.get("terminal_launch_args")
    external_session_id = snapshot.get("external_session_id")
    labels = snapshot.get("labels")
    labels = labels if isinstance(labels, dict) else {}
    fork_source = labels.get(FORK_SOURCE_EXTERNAL_SESSION_LABEL_KEY)
    cost_control_mode = snapshot.get("cost_control_mode_override")
    harness_override = snapshot.get("harness_override")
    routing_class = routing_class_from_snapshot(
        cost_control_mode=cost_control_mode if isinstance(cost_control_mode, str) else None,
        harness_override=harness_override if isinstance(harness_override, str) else None,
        labels={str(key): str(value) for key, value in labels.items()},
    )
    metadata = _ClaudeSessionLaunchMetadata(
        routing_enabled=routing_class.routing_enabled,
        auto_harness=routing_class.auto_harness,
        turn_routing=routing_class.turn_routing,
        reasoning_effort=effort if isinstance(effort, str) and effort else None,
        model_override=(
            model_override if isinstance(model_override, str) and model_override else None
        ),
        terminal_launch_args=(
            launch_args
            if isinstance(launch_args, list) and all(isinstance(arg, str) for arg in launch_args)
            else None
        ),
        external_session_id=(
            external_session_id
            if isinstance(external_session_id, str) and external_session_id
            else None
        ),
        fork_source_external_id=(
            fork_source if isinstance(fork_source, str) and fork_source else None
        ),
        fork_carry_history=labels.get(FORK_CARRY_HISTORY_LABEL_KEY) == "1",
    )
    _LOG.info(
        "Claude terminal launch config fetched: session=%s status=%s effort_set=%s "
        "model_override_set=%s launch_args_count=%d external_session_id_set=%s",
        session_id,
        response.status_code,
        metadata.reasoning_effort is not None,
        metadata.model_override is not None,
        len(metadata.terminal_launch_args or []),
        metadata.external_session_id is not None,
        extra={"session_id": session_id},
    )
    return metadata


async def _load_claude_launch_metadata(
    *,
    server_client: httpx.AsyncClient,
    session_id: str,
    session_init: RunnerSessionInitEnvelope | None,
) -> _ClaudeSessionLaunchMetadata:
    """Dispatch between the removable legacy and callback-free loaders."""
    if session_init is None:
        return await _load_legacy_claude_launch_metadata(server_client, session_id)
    metadata = _claude_launch_metadata_from_envelope(session_init)
    _LOG.info(
        "Claude terminal launch config loaded from init envelope: session=%s "
        "effort_set=%s model_override_set=%s launch_args_count=%d "
        "external_session_id_set=%s",
        session_id,
        metadata.reasoning_effort is not None,
        metadata.model_override is not None,
        len(metadata.terminal_launch_args or []),
        metadata.external_session_id is not None,
        extra={"session_id": session_id},
    )
    return metadata


def _typed_spawn_env(value: object) -> dict[str, str]:
    """Validate a dynamically resolved native spawn-env hook result."""
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise TypeError("native spawn-env builder must return a string mapping")
    return dict(value)


@dataclasses.dataclass(frozen=True)
class PreLaunchResult:
    """Outcome of a harness-specific pre-launch check (see the special arms).

    :param skip: When ``True``, do not auto-create (e.g. a sibling session's
        terminal is transferring in).
    :param force_recreate: When ``True``, tear down the session's terminals
        (``cleanup_conversation``, which is session-wide, not just this harness's
        terminal) and recreate — e.g. claude rebuild after an in-place agent
        switch. Mirrors the original claude arm's teardown scope.
    :param needs_terminal: When ``False``, skip auto-create because the session
        snapshot said a runner terminal is not needed (codex/antigravity).
    """

    skip: bool = False
    force_recreate: bool = False
    needs_terminal: bool = True


def _ensure_native_terminal_default_response(view: SessionResourceView) -> JSONResponse:
    """Default 200 response for the ensure path: the terminal view as-is."""
    return JSONResponse(status_code=200, content=session_resource_view_to_dict(view))


def _is_safe_bundle_segment(segment: Any) -> bool:
    """Return whether *segment* is a single, relative path component.

    Component check, not substring rejection: a directory legitimately
    named ``review..worker`` is one component and stays valid, while
    ``..``, ``a/b`` and absolute paths do not.
    """
    if not isinstance(segment, str) or not segment or segment in (".", ".."):
        return False
    try:
        candidate = Path(segment)
    except (TypeError, ValueError):
        return False
    return candidate.parts == (segment,) and not candidate.is_absolute()


def _sub_agent_bundle_segments(root: Any, child: Any) -> list[str] | None:
    """Identity-walk *child* up to *root*, collecting its bundle dir names.

    Returns ``None`` when the child is not reachable from the root by
    identity (synthetic / in-memory specs such as ``__web_researcher``)
    or when any hop lacks a usable ``source_rel_dir``.
    """
    parents: dict[int, Any] = {}
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        for sub in getattr(node, "sub_agents", None) or []:
            parents[id(sub)] = node
            stack.append(sub)
    segments: list[str] = []
    current = child
    while current is not root:
        parent = parents.get(id(current))
        if parent is None:
            return None
        segment = getattr(current, "source_rel_dir", None)
        if not _is_safe_bundle_segment(segment):
            return None
        segments.append(str(segment))
        current = parent
    segments.reverse()
    return segments
