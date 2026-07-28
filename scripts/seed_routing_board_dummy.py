#!/usr/bin/env python3
"""Seed Puppy Garden with demo routing cards, tasks, and inbox items."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = os.environ.get("OMNIGENT_SERVER_URL", "http://127.0.0.1:6767").rstrip("/")
MANAGER_AGENT_ID = os.environ.get(
    "SEED_MANAGER_AGENT_ID",
    "b89e2ff3b870d8c67827bc5db09a6b0b",
)
WORKER_AGENT_ID = os.environ.get(
    "SEED_WORKER_AGENT_ID",
    "f71a802cad50a02cbeb99952955a4ebe",
)
HOST_ID = os.environ.get("SEED_HOST_ID", "a443636bf8be4144ad01f31c6c3acb9f")
WORKSPACE = os.environ.get("SEED_WORKSPACE", os.path.expanduser("~/Project/omnigent-fork"))
BOOTSTRAP = {
    "host_id": HOST_ID,
    "workspace": WORKSPACE,
    "harness": "cursor-native",
    "model": "composer-2.5",
}
DISPATCH = {
    "worker_agent_id": WORKER_AGENT_ID,
    **BOOTSTRAP,
}


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


def _create_task(title: str, description: str, charter: str) -> str:
    task = _request(
        "POST",
        "/v1/agent-tasks",
        body={
            "manager_agent_id": MANAGER_AGENT_ID,
            "title": title,
            "description": description,
            "charter": charter,
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
            "summary": f"repo:{repo} pr:{pr} ci failure",
            "source": "seed:dummy",
            "source_key": f"{repo}#{pr}",
            "source_offset": offset_base,
            "tags": [{"tag_type": "repo", "tag": repo}, {"tag_type": "pr", "tag": str(pr)}],
            "payload": {"repo": repo, "pr_number": pr},
        },
        {
            "event_type": "github.pr.review_comment",
            "title": f"New comment on PR #{pr}",
            "summary": f"repo:{repo} pr:{pr} review comment",
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
    instructions: str,
    event_ids: list[str],
) -> None:
    package = _request(
        "POST",
        "/v1/agent-tasks/packages",
        body={
            "title": title,
            "manager_agent_id": MANAGER_AGENT_ID,
            "items": [
                {
                    "title": title,
                    "event_ids": event_ids,
                    "instructions": instructions,
                },
            ],
        },
    )
    print(f"  task package {title!r} → {package['id'][:8]}…")


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


def _create_inbox_item(task_id: str, title: str, instructions: str) -> None:
    item = _request(
        "POST",
        f"/v1/agent-tasks/{task_id}/items",
        body={
            "title": title,
            "instructions": instructions,
            "submit_for_user_ack": True,
            **DISPATCH,
        },
    )
    print(f"  inbox item {title!r} on {task_id[:8]}… → {item['id'][:8]}…")


def main() -> int:
    host_header = {"X-Omnigent-Host-Id": HOST_ID}
    offset_base = int(time.time()) % 1_000_000

    print("Creating managed tasks…")
    ci_task = _create_task(
        "omnigent-fork CI",
        "CI failures and PR reviews for omnigent-fork",
        "repo:omnigent-fork\nci\npull requests",
    )
    docs_task = _create_task(
        "docs refresh",
        "Documentation updates and changelog hygiene",
        "repo:omnigent-fork\ndocs\nmarkdown",
    )
    poll_task = _create_task(
        "poll plugins",
        "Host poll plugin maintenance",
        "poll_plugins\ngithub_pr\nwatchers",
    )

    print("Creating paused task packages…")
    _create_task_package(
        title="Fix CI on PR #891",
        instructions="Investigate lint failure and address review feedback on PR #891.",
        event_ids=_create_events(host_header, repo="omnigent-fork", pr=891, offset_base=offset_base),
    )
    _create_task_package(
        title="Update API docs for task routing",
        instructions="Refresh TASK_SECRETARY.md and API_REFERENCE after routing cards shipped.",
        event_ids=_create_events(host_header, repo="omnigent-fork", pr=902, offset_base=offset_base + 10),
    )
    _create_task_package(
        title="Fix github_pr poll plugin flake",
        instructions="Investigate intermittent false-positive PR state in poll plugin watcher.",
        event_ids=_create_events(host_header, repo="omnigent-fork", pr=915, offset_base=offset_base + 20),
    )
    _create_task_package(
        title="Investigate unrelated repo alert",
        instructions="Triage the alert and decide whether omnigent-fork needs changes.",
        event_ids=_create_events(host_header, repo="other-repo", pr=12, offset_base=offset_base + 30),
    )

    print("Creating FYI clusters…")
    fyi_events = _create_events(host_header, repo="dependabot-fork", pr=44, offset_base=offset_base + 40)
    _create_fyi_cluster(
        headline="Dependabot PR checks passed (unrelated repo)",
        rationale="Informational only — different repo, not tagged for you.",
        event_ids=fyi_events,
    )

    print("Creating inbox task items (#3 dispatch approval)…")
    _create_inbox_item(
        ci_task,
        "Re-run flaky integration test",
        "Re-run the agent-tasks integration suite and capture logs if it fails again.",
    )
    _create_inbox_item(
        ci_task,
        "Bump composer model pin",
        "Update the default task-worker model pin in constants and verify bootstrap.",
    )
    _create_inbox_item(
        docs_task,
        "Add routing card screenshot to README",
        "Capture Puppy Garden decisions section and add to agent-tasks README.",
    )
    _create_inbox_item(
        poll_task,
        "Add dedupe test for PR watcher",
        "Write a unit test ensuring duplicate check events do not create extra task events.",
    )

    board = _request("GET", "/v1/agent-tasks/board/pending")
    pending = board.get("pending", [])
    fyi = board.get("fyi", [])
    print(f"\nBoard pending packages: {len(pending)}")
    for card in pending:
        print(f"  - {card.get('headline')}")
    print(f"Board FYI clusters: {len(fyi)}")
    for card in fyi:
        print(f"  - {card.get('headline')}")

    for task_id, label in [
        (ci_task, "omnigent-fork CI"),
        (docs_task, "docs refresh"),
        (poll_task, "poll plugins"),
    ]:
        dash = _request("GET", f"/v1/agent-tasks/{task_id}/dashboard")
        inbox = len(dash.get("inbox_items", []))
        workers = sum(len(g.get("executions", [])) for g in dash.get("workers", []))
        print(f"  {label}: {inbox} inbox, {workers} work rows")

    print("\nDone — see **Pending** and **FYI** above task cards in Puppy Garden.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
