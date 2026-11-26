"""FYI clusters on the caller's GlobalHub board.

A manager files events that need no task into a cluster; the board lists the
pending clusters, and the user dismisses them. The board also lists the
watchers whose latest run could not read its sources.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import cast

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import FyiCluster, TaskEvent, Watcher
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes.agent_task_events import task_event_response
from omnigent.server.schemas import (
    AgentFyiClusterResponse,
    AgentFyiClusterState,
    AgentTaskBoardPending,
    AgentTaskWatcherProblem,
    AgentTaskWatcherProblemSource,
    DismissWatcherProblemsRequest,
    DismissWatcherProblemsResponse,
    FileAgentTaskEventsRequest,
)

# Events still waiting to be handled; a filed or dismissed one is settled.
_FILEABLE_STATES = ("unrouted", "routed")


def create_agent_task_fyi_router(
    board: BoardStores,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the FYI clusters on the caller's GlobalHub board.

    :param board: The board's events and FYI clusters.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    events = board.events
    clusters = board.fyi_clusters
    watchers = board.watchers

    def filed_events(cluster: FyiCluster, user_id: str | None) -> list[TaskEvent]:
        return events.list(owner_user_id=user_id, fyi_cluster_ids=[cluster.id])

    @router.get("/agent-tasks/board/pending", response_model=AgentTaskBoardPending)
    async def list_board_pending(request: Request) -> AgentTaskBoardPending:
        """List the caller's pending FYI clusters, oldest first, each with its events.

        Also list the caller's watchers whose latest run could not read its sources.
        """
        user_id = require_user(request, auth_provider)

        def load() -> tuple[list[tuple[FyiCluster, list[TaskEvent]]], list[Watcher]]:
            pending = clusters.list(owner_user_id=user_id, states=["pending"])
            filed: defaultdict[str, list[TaskEvent]] = defaultdict(list)
            if pending:
                cluster_ids = [cluster.id for cluster in pending]
                for event in events.list(owner_user_id=user_id, fyi_cluster_ids=cluster_ids):
                    if event.fyi_cluster_id is not None:
                        filed[event.fyi_cluster_id].append(event)
            troubled = [
                watcher
                for watcher in watchers.list(owner_user_id=user_id)
                if watcher.problem is not None
            ]
            troubled.sort(
                key=lambda watcher: watcher.problem.reported_at if watcher.problem else 0
            )
            return [(cluster, filed[cluster.id]) for cluster in pending], troubled

        listed, troubled = await asyncio.to_thread(load)
        return AgentTaskBoardPending(
            fyi=[_cluster_response(cluster, filed) for cluster, filed in listed],
            watcher_problems=[_watcher_problem_response(watcher) for watcher in troubled],
        )

    @router.post(
        "/agent-tasks/board/watcher-problems/dismiss",
        response_model=DismissWatcherProblemsResponse,
    )
    async def dismiss_watcher_problems(
        request: Request, body: DismissWatcherProblemsRequest
    ) -> DismissWatcherProblemsResponse:
        user_id = require_user(request, auth_provider)
        dismissed = await asyncio.to_thread(
            watchers.clear_problems, body.watcher_ids, owner_user_id=user_id
        )
        return DismissWatcherProblemsResponse(dismissed=dismissed)

    @router.post("/task-events/fyi-clusters", response_model=AgentFyiClusterResponse)
    async def file_events(
        request: Request, body: FileAgentTaskEventsRequest
    ) -> AgentFyiClusterResponse:
        """File events that need no task into an FYI cluster, all or none.

        Without ``cluster_id`` a new cluster holds them; with it, they join that
        pending cluster. Filing an event into its own cluster again is safe.
        """
        user_id = require_user(request, auth_provider)

        def file() -> tuple[FyiCluster, list[TaskEvent]]:
            target = None
            if body.cluster_id is not None:
                target = clusters.get(body.cluster_id, owner_user_id=user_id)
                if target is None:
                    raise OmnigentError("FYI cluster not found", code=ErrorCode.NOT_FOUND)
                if target.state != "pending":
                    raise OmnigentError(
                        f"FYI cluster {target.id} is {target.state}", code=ErrorCode.CONFLICT
                    )
            for event_id in body.event_ids:
                event = events.get(event_id, owner_user_id=user_id)
                if event is None:
                    raise OmnigentError(
                        f"task event {event_id} not found", code=ErrorCode.NOT_FOUND
                    )
                filed_here = (
                    target is not None
                    and event.state == "fyi"
                    and event.fyi_cluster_id == target.id
                )
                if event.state not in _FILEABLE_STATES and not filed_here:
                    raise OmnigentError(
                        f"task event {event_id} is {event.state}, not waiting to be handled",
                        code=ErrorCode.CONFLICT,
                    )
            if target is None:
                cluster = clusters.create(
                    uuid.uuid4().hex,
                    owner_user_id=user_id,
                    headline=body.headline,
                    event_ids=body.event_ids,
                    rationale=body.rationale,
                )
            else:
                cluster = clusters.extend(
                    target.id,
                    owner_user_id=user_id,
                    headline=body.headline,
                    event_ids=body.event_ids,
                    rationale=body.rationale,
                )
            if cluster is None:
                raise OmnigentError("task events changed while filing", code=ErrorCode.CONFLICT)
            return cluster, filed_events(cluster, user_id)

        cluster, filed = await asyncio.to_thread(file)
        return _cluster_response(cluster, filed)

    @router.post("/fyi-clusters/{cluster_id}/resolve", response_model=AgentFyiClusterResponse)
    async def resolve_cluster(request: Request, cluster_id: str) -> AgentFyiClusterResponse:
        """Dismiss one of the caller's FYI clusters and its events.

        Dismissing a cluster again changes nothing.
        """
        user_id = require_user(request, auth_provider)

        def resolve() -> tuple[FyiCluster, list[TaskEvent]]:
            cluster = clusters.dismiss(cluster_id, owner_user_id=user_id)
            if cluster is None:
                raise OmnigentError("FYI cluster not found", code=ErrorCode.NOT_FOUND)
            return cluster, filed_events(cluster, user_id)

        cluster, filed = await asyncio.to_thread(resolve)
        return _cluster_response(cluster, filed)

    return router


def _watcher_problem_response(watcher: Watcher) -> AgentTaskWatcherProblem:
    problem = watcher.problem
    assert problem is not None
    return AgentTaskWatcherProblem(
        watcher_id=watcher.id,
        watcher_name=watcher.name,
        session_id=watcher.conversation_id,
        sources=[
            AgentTaskWatcherProblemSource(
                source=item.source, source_key=item.source_key, reason=item.reason
            )
            for item in problem.sources
        ],
        note=problem.note,
        run_error=problem.run_error,
        reported_at=problem.reported_at,
    )


def _cluster_response(cluster: FyiCluster, filed: list[TaskEvent]) -> AgentFyiClusterResponse:
    return AgentFyiClusterResponse(
        id=cluster.id,
        headline=cluster.headline,
        rationale=cluster.rationale,
        state=cast(AgentFyiClusterState, cluster.state),
        created_at=cluster.created_at,
        resolved_at=cluster.resolved_at,
        events=[task_event_response(event) for event in filed],
    )
