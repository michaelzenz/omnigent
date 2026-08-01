#!/usr/bin/env python3
"""Poll plugin: github_pr — watch PR status and emit task events."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(os.environ["OMNIGENT_PLUGIN_DIR"])
STATE_PATH = PLUGIN_DIR / "state.json"
WATCHES_PATH = PLUGIN_DIR / "watches.json"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def post_task_event(**fields: object) -> None:
    import httpx

    base = os.environ["OMNIGENT_SERVER_URL"].rstrip("/")
    host_id = os.environ["OMNIGENT_HOST_ID"]
    resp = httpx.post(
        f"{base}/v1/task-events",
        headers={"X-Omnigent-Host-Id": host_id},
        json=fields,
        timeout=30.0,
    )
    resp.raise_for_status()


def gh_pr_snapshot(repo: str, pr_number: int) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repo,
                "--json",
                "state,mergedAt,statusCheckRollup,headRefOid,title",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout)


def checks_conclusion(snapshot: dict[str, Any]) -> str | None:
    rollup = snapshot.get("statusCheckRollup")
    if not isinstance(rollup, list) or not rollup:
        return None
    states = {item.get("state") for item in rollup if isinstance(item, dict)}
    if "FAILURE" in states:
        return "FAILURE"
    if states == {"SUCCESS"}:
        return "SUCCESS"
    return "PENDING"


def discover_auto_watches(auto_discover: list[str]) -> list[dict[str, Any]]:
    watches: list[dict[str, Any]] = []
    queries: list[tuple[str, str]] = []
    if "authored" in auto_discover:
        queries.append(("author:@me", "authored"))
    if "review_requested" in auto_discover:
        queries.append(("review-requested:@me", "review_requested"))
    for query, label in queries:
        try:
            proc = subprocess.run(
                ["gh", "search", "prs", query, "--state=open", "--json", "repository,number"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            continue
        if proc.returncode != 0:
            continue
        for row in json.loads(proc.stdout):
            repo = row.get("repository", {}).get("nameWithOwner")
            number = row.get("number")
            if repo and number is not None:
                watches.append({"repo": repo, "pr": number, "auto": label})
    return watches


def bound_task_id(context: dict[str, Any]) -> str | None:
    raw = context.get("task_id")
    if raw is None:
        return None
    task_id = str(raw).strip()
    return task_id or None


def emit_transition(
    *,
    plugin_name: str,
    repo: str,
    pr_number: int,
    event_type: str,
    title: str,
    source_offset: int,
    payload: dict[str, Any],
    task_id: str | None = None,
) -> None:
    fields: dict[str, object] = {
        "event_type": event_type,
        "title": title,
        "source": f"poll_plugin:{plugin_name}",
        "source_key": f"{repo}#{pr_number}",
        "source_offset": source_offset,
        "tags": [
            {"tag_type": "repo", "tag": repo},
            {"tag_type": "pr", "tag": str(pr_number)},
        ],
        "payload": payload,
    }
    if task_id is not None:
        fields["task_id"] = task_id
    post_task_event(**fields)


def main() -> int:
    plugin_name = os.environ.get("OMNIGENT_PLUGIN_NAME", PLUGIN_DIR.name)
    watches_doc = load_json(WATCHES_PATH, {"auto_discover": [], "explicit": []})
    auto_discover = watches_doc.get("auto_discover", [])
    explicit = watches_doc.get("explicit", [])
    targets: list[dict[str, Any]] = []
    if isinstance(auto_discover, list):
        targets.extend(discover_auto_watches([str(item) for item in auto_discover]))
    if isinstance(explicit, list):
        targets.extend(item for item in explicit if isinstance(item, dict))

    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}

    for target in targets:
        repo = target.get("repo")
        pr_number = target.get("pr")
        if not repo or pr_number is None:
            continue
        key = f"{repo}#{pr_number}"
        snapshot = gh_pr_snapshot(str(repo), int(pr_number))
        if snapshot is None:
            continue
        previous = state.get(key, {})
        merged_at = snapshot.get("mergedAt")
        checks = checks_conclusion(snapshot)
        context = target.get("context") if isinstance(target.get("context"), dict) else {}
        task_id = bound_task_id(context)

        if merged_at and not previous.get("mergedAt"):
            emit_transition(
                plugin_name=plugin_name,
                repo=str(repo),
                pr_number=int(pr_number),
                event_type="github.pr.merged",
                title=f"PR #{pr_number} merged in {repo}",
                source_offset=1,
                payload={
                    "repo": repo,
                    "pr_number": pr_number,
                    "merged_at": merged_at,
                    "context": context,
                },
                task_id=task_id,
            )
        elif checks == "FAILURE" and previous.get("checks") != "FAILURE":
            emit_transition(
                plugin_name=plugin_name,
                repo=str(repo),
                pr_number=int(pr_number),
                event_type="github.pr.checks_failed",
                title=f"PR #{pr_number} checks failed in {repo}",
                source_offset=2,
                payload={"repo": repo, "pr_number": pr_number, "context": context},
                task_id=task_id,
            )
        elif checks == "SUCCESS" and previous.get("checks") != "SUCCESS":
            emit_transition(
                plugin_name=plugin_name,
                repo=str(repo),
                pr_number=int(pr_number),
                event_type="github.pr.checks_passed",
                title=f"PR #{pr_number} checks passed in {repo}",
                source_offset=3,
                payload={"repo": repo, "pr_number": pr_number, "context": context},
                task_id=task_id,
            )

        state[key] = {
            "mergedAt": merged_at,
            "checks": checks,
            "headRefOid": snapshot.get("headRefOid"),
        }

    save_json(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
