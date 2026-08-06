#!/usr/bin/env python3
"""Seed Puppy Garden with demo routing cards, tasks, and worker lanes."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Literal

DEFAULT_BASE = os.environ.get("OMNIGENT_SERVER_URL", "http://127.0.0.1:6767").rstrip("/")
MANAGER_AGENT_NAME = os.environ.get("SEED_MANAGER_AGENT_NAME", "task-manager")
WORKER_AGENT_NAME = os.environ.get("SEED_WORKER_AGENT_NAME", "task-worker")
WORKER2_AGENT_NAME = os.environ.get("SEED_WORKER2_AGENT_NAME", "task-worker")
HOST_ID = os.environ.get("SEED_HOST_ID", "a443636bf8be4144ad01f31c6c3acb9f")
WORKSPACE = os.environ.get("SEED_WORKSPACE", os.path.expanduser("~/Project/omnigent-fork"))
BOOTSTRAP = {
    "host_id": HOST_ID,
    "workspace": WORKSPACE,
    "harness": "cursor-native",
    "model": "composer-2.5",
}
DISPATCH = {
    **BOOTSTRAP,
}
DISPATCH_WORKER2 = {
    **BOOTSTRAP,
}


def _resolve_agent_ids() -> tuple[str, str, str]:
    listed = _request("GET", "/v1/agents?limit=200")
    by_name = {row["name"]: row["id"] for row in listed.get("data", [])}
    manager_id = by_name.get(MANAGER_AGENT_NAME)
    worker_id = by_name.get(WORKER_AGENT_NAME)
    worker2_id = by_name.get(WORKER2_AGENT_NAME)
    missing = [
        name
        for name, value in (
            (MANAGER_AGENT_NAME, manager_id),
            (WORKER_AGENT_NAME, worker_id),
            (WORKER2_AGENT_NAME, worker2_id),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing agents after server start: {', '.join(missing)}")
    return manager_id, worker_id, worker2_id


def _request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    url = f"{DEFAULT_BASE}{path}"
    data = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from exc


def _create_task(title: str, description: str, internal_note: str, *, agent_profile_id: str) -> str:
    task = _request(
        "POST",
        "/v1/agent-tasks",
        body={
            "agent_profile_id": agent_profile_id,
            "title": title,
            "description": description,
            "internal_note": internal_note,
        },
    )
    task_id = task["id"]
    _request("POST", f"/v1/agent-tasks/{task_id}/bootstrap", body=BOOTSTRAP)
    print(f"  task {title!r} → {task_id[:8]}…")
    return task_id


def _create_events(
    host_header: dict[str, str],
    *,
    repo: str,
    pr: int,
    offset_base: int,
) -> list[str]:
    specs = [
        {
            "event_type": "github.pr.checks_failed",
            "title": f"PR #{pr} checks failed",
            "source": "seed:dummy",
            "source_key": f"{repo}#{pr}",
            "source_offset": offset_base,
            "tags": [{"tag_type": "repo", "tag": repo}, {"tag_type": "pr", "tag": str(pr)}],
            "payload": {"repo": repo, "pr_number": pr},
        },
        {
            "event_type": "github.pr.review_comment",
            "title": f"New comment on PR #{pr}",
            "source": "seed:dummy",
            "source_key": f"{repo}#{pr}",
            "source_offset": offset_base + 1,
            "tags": [{"tag_type": "repo", "tag": repo}, {"tag_type": "pr", "tag": str(pr)}],
            "payload": {"repo": repo, "pr_number": pr},
        },
    ]
    event_ids: list[str] = []
    for spec in specs:
        created = _request("POST", "/v1/task-events", body=spec, headers=host_header)
        event_ids.append(created["id"])
    return event_ids


def _create_task_package(
    *,
    title: str,
    description: str,
    instructions: str,
    internal_note: str | None = None,
    event_ids: list[str],
    agent_profile_id: str,
    asset_urls: list[tuple[str, str]] | None = None,
) -> str:
    package = _request(
        "POST",
        "/v1/agent-tasks/packages",
        body={
            "title": title,
            "agent_profile_id": agent_profile_id,
            "items": [
                {
                    "title": title,
                    "event_ids": event_ids,
                    "description": description,
                    "instructions": instructions,
                    "internal_note": internal_note,
                },
            ],
        },
    )
    task_id = package["id"]
    print(f"  task package {title!r} → {task_id[:8]}…")
    for asset_title, url in asset_urls or ():
        _create_task_asset(task_id, asset_title, url)
    return task_id


def _create_fyi_cluster(
    *,
    headline: str,
    rationale: str,
    event_ids: list[str],
) -> None:
    cluster = _request(
        "POST",
        "/v1/task-events/fyi-clusters",
        body={
            "headline": headline,
            "rationale": rationale,
            "event_ids": event_ids,
        },
    )
    print(f"  fyi cluster {headline!r} → {cluster['id'][:8]}…")


def _create_task_asset(task_id: str, title: str, url: str) -> int:
    asset = _request(
        "POST",
        f"/v1/agent-tasks/{task_id}/assets",
        body={
            "kind": "url",
            "title": title,
            "url": url,
        },
    )
    print(f"  asset {title!r} → {asset['id']}")
    return asset["id"]


def _create_unassigned_inbox_item(task_id: str, title: str, instructions: str) -> str:
    item = _request(
        "POST",
        f"/v1/agent-tasks/{task_id}/items",
        body={
            "title": title,
            "instructions": instructions,
            "submit_for_user_ack": True,
        },
    )
    print(f"  unassigned inbox {title!r} → {item['id'][:8]}…")
    return item["id"]


def _create_assigned_inbox_item(
    task_id: str,
    title: str,
    instructions: str,
    *,
    worker_profile_id: str,
) -> str:
    item = _request(
        "POST",
        f"/v1/agent-tasks/{task_id}/items",
        body={
            "title": title,
            "instructions": instructions,
            "submit_for_user_ack": True,
            "worker_profile_id": worker_profile_id,
            **BOOTSTRAP,
        },
    )
    print(f"  assigned pending {title!r} ({worker_profile_id[:8]}…) → {item['id'][:8]}…")
    return item["id"]


def _dispatch_item(item_id: str, *, dispatch: dict | None = None) -> None:
    body = _request("POST", f"/v1/task-items/{item_id}/dispatch", body=dispatch or DISPATCH)
    print(f"  dispatched {item_id[:8]}… → {body.get('conversation_id', '')[:8]}…")


def _accept_item(item_id: str) -> None:
    body = _request(
        "POST",
        f"/v1/task-items/{item_id}/resolve",
        body={"resolution": "accept_item"},
    )
    print(f"  accepted {item_id[:8]}… → {body.get('state')}")


def _seed_rich_ci_task(ci_task: str, *, worker_profile_id: str, worker2_profile_id: str) -> None:
    print("Seeding rich CI worker lanes…")
    _create_unassigned_inbox_item(
        ci_task,
        "Triage unknown failure",
        "No worker picked yet — assign CI Fixer or Docs agent in inbox.",
    )
    _create_unassigned_inbox_item(
        ci_task,
        "Review dependabot bump",
        "Decide whether to route to CI or docs after scanning the diff.",
    )

    _create_assigned_inbox_item(
        ci_task,
        "Fix flaky integration test",
        "Re-run agent-tasks integration suite and capture logs.",
        worker_profile_id=worker_profile_id,
    )
    _create_assigned_inbox_item(
        ci_task,
        "Update changelog entry",
        "Add a routing-board entry to CHANGELOG after UI lands.",
        worker_profile_id=worker2_profile_id,
    )

    running = _request(
        "POST",
        f"/v1/agent-tasks/{ci_task}/items",
        body={
            "title": "Investigate lint failure on main",
            "instructions": "Read CI logs and patch the failing module.",
            "state": "queued",
            **DISPATCH,
        },
    )
    _dispatch_item(running["id"])

    queued = _request(
        "POST",
        f"/v1/agent-tasks/{ci_task}/items",
        body={
            "title": "Retry upload stress test",
            "instructions": "Queue for CI Fixer after the lint run completes.",
            "state": "queued",
            **DISPATCH,
        },
    )
    print(f"  queued item {queued['title']!r} → {queued['id'][:8]}…")

    _create_parked_item(
        ci_task,
        "Docs follow-up (interrupted)",
        "Update the runbook with the new retry policy.",
        state="interrupted",
        dispatch=DISPATCH,
    )

    _seed_dispatch_failed_items(
        ci_task,
        worker_profile_id=worker_profile_id,
        worker2_profile_id=worker2_profile_id,
    )

    history_specs = [
        ("Land green checks on PR #880", "Fixed retry logic and updated tests."),
        ("Silence noisy codecov comment", "Adjusted coverage threshold in workflow."),
        ("Patch nightly flake", "Stabilized timer test ordering."),
    ]
    for title, instructions in history_specs:
        item = _request(
            "POST",
            f"/v1/agent-tasks/{ci_task}/items",
            body={
                "title": title,
                "instructions": instructions,
                "state": "queued",
                **DISPATCH,
            },
        )
        _dispatch_item(item["id"])
        _finish_execution_for_item(item["id"], summary=instructions)
        print(f"  completed history {title!r}")

    _create_task_asset(
        ci_task,
        "PR #891",
        "https://github.com/databricks/omnigent-fork/pull/891",
    )
    _create_task_asset(
        ci_task,
        "CI workflow (main)",
        "https://github.com/databricks/omnigent-fork/actions",
    )
    _create_task_asset(
        ci_task,
        "Latest failing run",
        "https://github.com/databricks/omnigent-fork/actions/runs/1234567890",
    )


def _set_item_state(item_id: str, state: str) -> None:
    db_uri = os.environ.get(
        "OMNIGENT_DATABASE_URI",
        f"sqlite:///{os.path.expanduser('~/.omnigent/chat.db')}",
    )
    from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore

    item_store = SqlAlchemyTaskItemStore(db_uri)
    item_store.update_item(item_id, state=state)
    print(f"  set {item_id[:8]}… → {state}")


def _create_parked_item(
    task_id: str,
    title: str,
    instructions: str,
    *,
    state: Literal["interrupted", "dispatch_failed"],
    dispatch: dict,
) -> str:
    item = _request(
        "POST",
        f"/v1/agent-tasks/{task_id}/items",
        body={
            "title": title,
            "instructions": instructions,
            "state": "queued",
            **dispatch,
        },
    )
    _set_item_state(item["id"], state)
    return item["id"]


def _seed_dispatch_failed_items(
    task_id: str,
    *,
    worker_profile_id: str,
    worker2_profile_id: str,
) -> None:
    print("Seeding dispatch-failed worker lanes…")
    specs = [
        (
            "Spawn review worker (dispatch failed)",
            "Runner never accepted the dispatch — retry after host reconnects.",
            {"worker_profile_id": worker2_profile_id, **BOOTSTRAP},
        ),
        (
            "Start codecov fixer (dispatch failed)",
            "Dispatch timed out waiting for an idle worker slot.",
            {"worker_profile_id": worker_profile_id, **BOOTSTRAP},
        ),
        (
            "Route security scan (dispatch failed)",
            "Host reported harness unavailable during dispatch.",
            {"worker_profile_id": worker2_profile_id, **BOOTSTRAP},
        ),
        (
            "Enqueue nightly flake repro (dispatch failed)",
            "Queue halted after the previous dispatch failure on this lane.",
            {"worker_profile_id": worker_profile_id, **BOOTSTRAP},
        ),
    ]
    for title, instructions, dispatch in specs:
        _create_parked_item(
            task_id,
            title,
            instructions,
            state="dispatch_failed",
            dispatch=dispatch,
        )


def _finish_execution_for_item(item_id: str, *, summary: str) -> None:
    db_uri = os.environ.get(
        "OMNIGENT_DATABASE_URI",
        f"sqlite:///{os.path.expanduser('~/.omnigent/chat.db')}",
    )
    from omnigent.agent_tasks.executions import complete_execution
    from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
    from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore

    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    executions = event_store.list_executions_for_item(item_id)
    if not executions:
        return
    execution = executions[-1]
    complete_execution(
        event_store,
        execution.id,
        status="succeeded",
        result_summary=summary,
    )
    item_store.update_item(item_id, state="done")


def _demo_worker_id(index: int) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"seed-twenty-workers-{index:02d}").hex


# Worker 01 → cb7784…; used for inner-scroll / lane row load tests.
_HEAVY_WORKER_INDEX = 1
_HEAVY_WORKER_ITEM_COUNT = 10
_LOAD_TEST_ASSET_COUNT = 40


def _seed_twenty_worker_task(*, agent_profile_id: str) -> str:
    """Create one active task with twenty distinct worker lanes."""
    print("Seeding 20-worker load test task…")
    task_id = _create_task(
        "20-worker load test",
        "Scroll and accordion stress test with twenty worker lanes",
        "load-test\nworkers\nui",
        agent_profile_id=agent_profile_id,
    )
    for index in range(1, 21):
        worker_id = _demo_worker_id(index)
        dispatch = {**BOOTSTRAP, "worker_profile_id": worker_id}
        if index == _HEAVY_WORKER_INDEX:
            running = _request(
                "POST",
                f"/v1/agent-tasks/{task_id}/items",
                body={
                    "title": f"Worker {index:02d} item 01 (running)",
                    "instructions": "Active lane for accordion default expansion.",
                    "state": "queued",
                    **dispatch,
                },
            )
            _dispatch_item_with(dispatch, running["id"])
            for item_index in range(2, _HEAVY_WORKER_ITEM_COUNT + 1):
                _request(
                    "POST",
                    f"/v1/agent-tasks/{task_id}/items",
                    body={
                        "title": f"Worker {index:02d} item {item_index:02d}",
                        "instructions": f"Queued backlog item {item_index} for worker {index:02d}.",
                        "state": "queued",
                        **dispatch,
                    },
                )
            continue
        _request(
            "POST",
            f"/v1/agent-tasks/{task_id}/items",
            body={
                "title": f"Worker {index:02d} queued job",
                "instructions": f"Queued backlog for worker lane {index}.",
                "state": "queued",
                **dispatch,
            },
        )
    for asset_index in range(1, _LOAD_TEST_ASSET_COUNT + 1):
        _create_task_asset(
            task_id,
            f"Load test link {asset_index:02d}",
            f"https://example.com/load-test/{asset_index:02d}",
        )
    dash = _request("GET", f"/v1/agent-tasks/{task_id}/dashboard")
    workers = len(dash.get("workers", []))
    assets = len(dash.get("assets", []))
    heavy_lane = next(
        (lane for lane in dash.get("workers", []) if lane.get("worker_agent_id", "").startswith("cb7784")),
        None,
    )
    heavy_rows = len(heavy_lane.get("rows", [])) if heavy_lane else 0
    print(f"  20-worker load test: {workers} workers, {assets} assets, heavy lane rows={heavy_rows}")
    if workers != 20:
        print(f"  warning: expected 20 worker lanes, got {workers} (stale workers from prior seeds?)")
    if assets != _LOAD_TEST_ASSET_COUNT:
        raise RuntimeError(f"Expected {_LOAD_TEST_ASSET_COUNT} assets, got {assets}")
    if heavy_rows != _HEAVY_WORKER_ITEM_COUNT:
        print(f"  warning: expected {_HEAVY_WORKER_ITEM_COUNT} items on heavy worker, got {heavy_rows}")
    return task_id


def _dispatch_item_with(dispatch: dict, item_id: str) -> None:
    body = _request("POST", f"/v1/task-items/{item_id}/dispatch", body=dispatch)
    print(f"  dispatched {item_id[:8]}… → {body.get('conversation_id', '')[:8]}…")


def main() -> int:
    host_header = {"X-Omnigent-Host-Id": HOST_ID}
    offset_base = int(time.time()) % 1_000_000
    manager_profile_id, worker_profile_id, worker2_profile_id = _resolve_agent_ids()
    global DISPATCH, DISPATCH_WORKER2
    DISPATCH = {"worker_profile_id": worker_profile_id, **BOOTSTRAP}
    DISPATCH_WORKER2 = {"worker_profile_id": worker2_profile_id, **BOOTSTRAP}

    print("Creating managed tasks…")
    ci_task = _create_task(
        "omnigent-fork CI",
        "CI failures and PR reviews for omnigent-fork",
        "repo:omnigent-fork\nci\npull requests",
        agent_profile_id=manager_profile_id,
    )
    docs_task = _create_task(
        "docs refresh",
        "Documentation updates and changelog hygiene",
        "repo:omnigent-fork\ndocs\nmarkdown",
        agent_profile_id=manager_profile_id,
    )
    poll_task = _create_task(
        "poll plugins",
        "Host poll plugin maintenance",
        "poll_plugins\ngithub_pr\nwatchers",
        agent_profile_id=manager_profile_id,
    )

    _seed_rich_ci_task(
        ci_task,
        worker_profile_id=worker_profile_id,
        worker2_profile_id=worker2_profile_id,
    )
    twenty_worker_task = _seed_twenty_worker_task(agent_profile_id=manager_profile_id)

    print("Creating pending task packages…")
    _create_task_package(
        title="Fix CI on PR #891",
        description="CI failed on your open PR and reviewers asked for lint fixes.",
        instructions="Investigate lint failure and address review feedback on PR #891.",
        internal_note="PR #891, repo omnigent-fork. Lint job failed on main merge base.",
        event_ids=_create_events(host_header, repo="omnigent-fork", pr=891, offset_base=offset_base),
        agent_profile_id=manager_profile_id,
        asset_urls=[
            ("PR #891", "https://github.com/databricks/omnigent-fork/pull/891"),
            ("CI checks", "https://github.com/databricks/omnigent-fork/actions"),
        ],
    )
    _create_task_package(
        title="Update API docs for task routing",
        description="Routing UI shipped; docs still describe the old inbox flow.",
        instructions="Refresh TASK_BROKER.md and API_REFERENCE after routing cards shipped.",
        internal_note="See PR #902 and docs/agent-tasks/ for current API shapes.",
        event_ids=_create_events(host_header, repo="omnigent-fork", pr=902, offset_base=offset_base + 10),
        agent_profile_id=manager_profile_id,
        asset_urls=[
            ("API reference", "https://github.com/databricks/omnigent-fork/blob/main/docs/agent-tasks/API_REFERENCE.md"),
            ("PR #902", "https://github.com/databricks/omnigent-fork/pull/902"),
        ],
    )
    _create_task_package(
        title="Fix github_pr poll plugin flake",
        description="Poll plugin reported a stale PR state that blocked routing.",
        instructions="Investigate intermittent false-positive PR state in poll plugin watcher.",
        internal_note="Repro linked from PR #915 comments; watcher host poll_plugins.",
        event_ids=_create_events(host_header, repo="omnigent-fork", pr=915, offset_base=offset_base + 20),
        agent_profile_id=manager_profile_id,
        asset_urls=[
            ("PR #915", "https://github.com/databricks/omnigent-fork/pull/915"),
            ("Poll plugin code", "https://github.com/databricks/omnigent-fork/tree/main/omnigent/host/polling"),
        ],
    )
    _create_task_package(
        title="Investigate unrelated repo alert",
        description="Alert fired from another repo; confirm whether we own the fix.",
        instructions="Triage the alert and decide whether omnigent-fork needs changes.",
        internal_note="other-repo PR #12; no omnigent-fork code touched yet.",
        event_ids=_create_events(host_header, repo="other-repo", pr=12, offset_base=offset_base + 30),
        agent_profile_id=manager_profile_id,
        asset_urls=[
            ("other-repo PR #12", "https://github.com/example/other-repo/pull/12"),
            ("omnigent-fork (reference)", "https://github.com/databricks/omnigent-fork"),
        ],
    )

    print("Creating FYI clusters…")
    fyi_events = _create_events(host_header, repo="dependabot-fork", pr=44, offset_base=offset_base + 40)
    _create_fyi_cluster(
        headline="Dependabot PR checks passed (unrelated repo)",
        rationale="Informational only — different repo, not tagged for you.",
        event_ids=fyi_events,
    )

    print("Creating docs/poll inbox samples…")
    _create_unassigned_inbox_item(
        docs_task,
        "Screenshot new worker lanes",
        "Capture the accordion board for the README demo section.",
    )
    _create_assigned_inbox_item(
        docs_task,
        "Polish API_REFERENCE worker section",
        "Document dashboard worker lanes after deploy.",
        worker_profile_id=worker2_profile_id,
    )
    _create_task_asset(
        docs_task,
        "Routing board README",
        "https://github.com/databricks/omnigent-fork/blob/main/docs/agent-tasks/README.md",
    )
    _create_unassigned_inbox_item(
        poll_task,
        "Pick owner for PR watcher dedupe",
        "Assign to CI Fixer or create a new worker for poll plugins.",
    )
    _create_assigned_inbox_item(
        poll_task,
        "Add dedupe test for PR watcher",
        "Unit test duplicate check events do not create extra task events.",
        worker_profile_id=worker_profile_id,
    )
    _create_parked_item(
        docs_task,
        "Publish docs preview (dispatch failed)",
        "Preview host rejected the workspace path during dispatch.",
        state="dispatch_failed",
        dispatch=DISPATCH_WORKER2,
    )
    _create_parked_item(
        poll_task,
        "Restart poll plugin host (dispatch failed)",
        "Could not reach the poll_plugins harness on the configured host.",
        state="dispatch_failed",
        dispatch=DISPATCH,
    )

    pending = _request("GET", "/v1/agent-tasks?state=pending&limit=100")
    fyi_board = _request("GET", "/v1/agent-tasks/board/pending")
    pending_tasks = pending.get("data", [])
    fyi = fyi_board.get("fyi", [])
    print(f"\nPending packages: {len(pending_tasks)}")
    for task in pending_tasks:
        print(f"  - {task.get('title')}")
    print(f"Board FYI clusters: {len(fyi)}")
    for card in fyi:
        print(f"  - {card.get('headline')}")

    for task_id, label in [
        (ci_task, "omnigent-fork CI"),
        (twenty_worker_task, "20-worker load test"),
        (docs_task, "docs refresh"),
        (poll_task, "poll plugins"),
    ]:
        dash = _request("GET", f"/v1/agent-tasks/{task_id}/dashboard")
        inbox = len(dash.get("inbox_items", []))
        workers = len(dash.get("workers", []))
        rows = sum(len(w.get("rows", [])) for w in dash.get("workers", []))
        print(f"  {label}: {inbox} unassigned inbox, {workers} workers, {rows} lane rows")

    print("\nDone — open Puppy Garden to review worker lanes and unassigned inbox.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
