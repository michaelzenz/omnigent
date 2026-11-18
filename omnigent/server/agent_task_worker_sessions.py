"""Start a session that works on one of a user's GlobalHub tasks.

The session is created the way main's scheduled tasks create one on a connected
host: main's session-create checks run first, then an optional git worktree,
the session row and the owner's grant. The session becomes the task's worker,
followed by the task's manager, before its first message, so the manager hears
of each finished turn.
The first message goes out in the background, because a runner can take longer
to start than a tool call waits. A message that cannot be sent becomes an event.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_named_project
from omnigent.agent_tasks.roles import BUILTIN_ROLES, WATCHER_ROLE
from omnigent.agent_tasks.worker_subscriptions import follow_workers
from omnigent.entities import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.host.git_worktree import WorktreeError, validate_branch_name
from omnigent.native.native_coding_agents import native_coding_agent_for_agent_name
from omnigent.server.agent_queue_dispatcher import Sender
from omnigent.server.agent_task_delivery import SendResult, SessionSendDeps, send_to_session
from omnigent.server.agent_task_session_events import SessionEventReporter
from omnigent.server.auth import LEVEL_OWNER, RESERVED_USER_LOCAL
from omnigent.server.routes._agent_task_callers import runner_bound_caller
from omnigent.server.routes._host_worktree import (
    WORKTREE_ROOT_LABEL_KEY,
    CreatedWorktree,
    worktree_root_fingerprint,
)
from omnigent.server.routes._session_create_validation import (
    validate_existing_host_workspace,
    validate_session_agent,
    validate_session_model_metadata,
)
from omnigent.server.routes._sessions.helpers import (
    _announce_session_added,
    _create_session_worktree,
    _remove_session_worktree_best_effort,
    _repl_terminal_ui_labels,
)
from omnigent.server.schemas import StartAgentTaskWorkerRequest, StartedAgentTaskWorker
from omnigent.server.session_orchestration import require_named_source_private
from omnigent.stores.conversation_store import CREATED_BY_SESSION_LABEL_KEY

if TYPE_CHECKING:
    from omnigent.runtime.agent_cache import AgentCache

_logger = logging.getLogger(__name__)

# GlobalHub's own role agents act on the board, so they never work on a task.
_ROLE_AGENT_NAMES = frozenset(role.agent_name for role in (*BUILTIN_ROLES, WATCHER_ROLE))


class WorkerSessionStarter:
    """Starts the sessions that work on users' tasks and sends their first messages."""

    def __init__(
        self,
        *,
        board: BoardStores,
        sessions: RoleSessionStores,
        send_deps: SessionSendDeps,
        reporter: SessionEventReporter,
        agent_cache: AgentCache | None,
        send: Sender = send_to_session,
    ) -> None:
        """
        :param board: Holds the tasks and their workers.
        :param sessions: Holds the sessions, agents, projects and grants.
        :param send_deps: How the first message reaches the session's runner.
        :param reporter: Reports a first message that could not be sent.
        :param agent_cache: Loads an agent's workspace boundary.
        :param send: Sends a message into a session.
        """
        self._board = board
        self._sessions = sessions
        self._send_deps = send_deps
        self._reporter = reporter
        self._agent_cache = agent_cache
        self._send = send
        self._sends: set[asyncio.Task[None]] = set()

    async def start(
        self,
        request: Request,
        task_id: str,
        body: StartAgentTaskWorkerRequest,
        *,
        user_id: str | None,
    ) -> StartedAgentTaskWorker:
        """
        Start a session for one of the user's tasks and attach it as a worker.

        A request from an agent must come from a private session through its own
        runner, as main requires for an agent that starts other sessions.

        :param request: The request; its app holds the hosts.
        :param task_id: One of the user's tasks.
        :param body: Where the session runs, what it runs, and its first message.
        :param user_id: The owner, or ``None`` in single-user mode.
        :returns: The session and its worker. The first message is on its way.
        :raises OmnigentError: ``NOT_FOUND`` when the task is missing or someone
            else's; ``FORBIDDEN`` for a calling session shared with edit access
            or another user's host; ``INVALID_INPUT`` for an unknown agent, a
            workspace or model that fails main's checks, or a refused worktree.
        """
        sessions = self._sessions
        conversations = sessions.conversation_store
        await asyncio.to_thread(
            require_named_source_private,
            request,
            user_id,
            conversations,
            sessions.permission_store,
        )
        caller = await asyncio.to_thread(runner_bound_caller, request, conversations)
        if await asyncio.to_thread(self._board.tasks.get, task_id, owner_user_id=user_id) is None:
            raise OmnigentError("task not found", code=ErrorCode.NOT_FOUND)
        if body.project_name is not None and sessions.project_store is None:
            raise OmnigentError(
                "This server has no projects; omit project_name", code=ErrorCode.INVALID_INPUT
            )
        agent = await self._agent(request, body.agent, user_id)
        model, effort = validate_session_model_metadata(
            model_override=body.model, reasoning_effort=body.reasoning_effort
        )
        workspace = await validate_existing_host_workspace(
            user_id=user_id,
            host_id=body.host_id,
            workspace=body.workspace,
            agent=agent,
            agent_cache=self._agent_cache,
            host_store=getattr(request.app.state, "host_store", None),
            host_registry=getattr(request.app.state, "host_registry", None),
        )
        labels = await self._presentation_labels(agent)
        if caller is not None:
            labels[CREATED_BY_SESSION_LABEL_KEY] = caller.id
        worktree, git_branch, workspace = await self._worktree(request, body, workspace)
        if worktree is not None:
            labels[WORKTREE_ROOT_LABEL_KEY] = worktree_root_fingerprint(worktree.worktree_path)
        try:
            conv, worker_id = await self._create(
                body,
                task_id=task_id,
                user_id=user_id,
                agent_id=agent.id,
                workspace=workspace,
                git_branch=git_branch,
                labels=labels,
                model=model,
                effort=effort,
            )
        except Exception:
            if worktree is not None:
                await _remove_session_worktree_best_effort(
                    host_id=body.host_id,
                    worktree_path=worktree.worktree_path,
                    branch=worktree.branch,
                    # A worktree of an existing branch keeps the branch and its commits.
                    delete_branch=body.git is None or not body.git.existing_branch,
                    host_registry=getattr(request.app.state, "host_registry", None),
                    reason="worker-start-rollback",
                )
            raise
        _announce_session_added(user_id, conv.id)
        self._send_first_message(conv.id, body.message, user_id)
        return StartedAgentTaskWorker(
            worker_id=worker_id,
            task_id=task_id,
            session_id=conv.id,
            title=body.title,
            agent=agent.name,
            host_id=body.host_id,
            workspace=workspace,
            git_branch=git_branch,
            project_id=conv.project_id,
        )

    async def stop(self) -> None:
        """Cancel the first messages still on their way."""
        for sending in self._sends:
            sending.cancel()
        await asyncio.gather(*self._sends, return_exceptions=True)

    async def _agent(self, request: Request, requested: str | None, user_id: str | None) -> Any:
        """Return the named agent, else the server's New Chat default agent."""
        curation = getattr(request.app.state, "picker_curation", None)
        name = requested or getattr(curation, "default_agent_name", None)
        if name is None:
            raise OmnigentError(
                "Name an agent: this server has no default agent (default_agent_name)",
                code=ErrorCode.INVALID_INPUT,
            )
        if name in _ROLE_AGENT_NAMES:
            raise OmnigentError(
                f"{name!r} runs GlobalHub roles; name another agent", code=ErrorCode.INVALID_INPUT
            )
        sessions = self._sessions
        found = await asyncio.to_thread(sessions.agent_store.get_by_name, name)
        if found is None:
            raise OmnigentError(f"No agent named {name!r}", code=ErrorCode.INVALID_INPUT)
        return await validate_session_agent(
            user_id=user_id,
            agent_id=found.id,
            agent_store=sessions.agent_store,
            permission_store=sessions.permission_store,
            conversation_store=sessions.conversation_store,
        )

    async def _presentation_labels(self, agent: Any) -> dict[str, str]:
        """Return the labels that give the session the terminal view New Chat gives it."""
        native = native_coding_agent_for_agent_name(agent.name)
        if native is not None:
            return dict(native.presentation_labels)
        try:
            return await asyncio.to_thread(
                _repl_terminal_ui_labels,
                agent=agent,
                agent_cache=self._agent_cache,
                harness_override=None,
            )
        except Exception:
            # Without the labels the session shows only its chat view.
            _logger.exception("GlobalHub could not read the terminal view of agent %s", agent.id)
            return {}

    async def _worktree(
        self, request: Request, body: StartAgentTaskWorkerRequest, workspace: str
    ) -> tuple[CreatedWorktree | None, str | None, str]:
        """Apply ``body.git``: return the worktree created, the branch, and the workspace."""
        git = body.git
        if git is None:
            return None, None, workspace
        if git.existing_worktree:
            # The workspace is the worktree already; only its branch is recorded.
            try:
                validate_branch_name(git.branch_name)
            except WorktreeError as exc:
                raise OmnigentError(exc.message, code=ErrorCode.INVALID_INPUT) from exc
            return None, git.branch_name, workspace
        created = await _create_session_worktree(
            host_id=body.host_id, source_repo=workspace, git=git, request=request
        )
        return created, created.branch, created.workspace or created.worktree_path

    async def _create(
        self,
        body: StartAgentTaskWorkerRequest,
        *,
        task_id: str,
        user_id: str | None,
        agent_id: str,
        workspace: str,
        git_branch: str | None,
        labels: dict[str, str],
        model: str | None,
        effort: str | None,
    ) -> tuple[Conversation, str]:
        """Create the session, give the user ownership, and adopt it; return it and its worker."""
        sessions = self._sessions
        project_id = (
            await asyncio.to_thread(
                ensure_named_project, sessions.project_store, user_id, body.project_name
            )
            if body.project_name is not None
            else None
        )
        conv = await asyncio.to_thread(
            sessions.conversation_store.create_conversation,
            agent_id=agent_id,
            title=body.title,
            host_id=body.host_id,
            workspace=workspace,
            git_branch=git_branch,
            project_id=project_id,
            labels=labels,
            model_override=model,
            reasoning_effort=effort,
        )
        try:
            worker_id = await asyncio.to_thread(
                self._own_and_adopt, conv.id, task_id, body.title, user_id
            )
        except Exception:
            await sessions.conversation_store.delete_conversation(conv.id)
            raise
        return conv, worker_id

    def _own_and_adopt(
        self, session_id: str, task_id: str, title: str, user_id: str | None
    ) -> str:
        permissions = self._sessions.permission_store
        if permissions is not None and user_id is not None:
            permissions.ensure_user(user_id)
            permissions.grant(user_id, session_id, LEVEL_OWNER)
        worker = self._board.workers.create(
            uuid.uuid4().hex,
            owner_user_id=user_id,
            task_id=task_id,
            session_id=session_id,
            title=title,
        )
        task = self._board.tasks.get(task_id, owner_user_id=user_id)
        if task is not None:
            follow_workers(self._board, task, owner_user_id=user_id, session_ids=[session_id])
        return worker.id

    def _send_first_message(self, session_id: str, message: str, user_id: str | None) -> None:
        sending = asyncio.create_task(
            self._deliver_first_message(session_id, message, user_id),
            name=f"globalhub-worker-start-{session_id}",
        )
        self._sends.add(sending)
        sending.add_done_callback(self._sends.discard)

    async def _deliver_first_message(
        self, session_id: str, message: str, user_id: str | None
    ) -> None:
        try:
            result = await self._send(
                session_id,
                message,
                created_by=user_id or RESERVED_USER_LOCAL,
                deps=self._send_deps,
            )
        except Exception as exc:
            _logger.exception("GlobalHub could not send the first message to %s", session_id)
            result = SendResult("failed", str(exc) or type(exc).__name__)
        # A session deleted meanwhile needs no report.
        if result.outcome in ("sent", "gone"):
            return
        error = result.error or "a turn was already running, so the message was not sent"
        _logger.warning(
            "GlobalHub could not send the first message to session %s: %s", session_id, error
        )
        try:
            await asyncio.to_thread(self._reporter.report_start_failed, session_id, error)
        except Exception:
            _logger.exception("GlobalHub could not report the failed start of %s", session_id)
