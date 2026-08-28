#!/usr/bin/env python3
"""Poll plugin: github_pr — watch PR status + comments and emit task events."""

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


# ── GitHub helpers (gh CLI) ────────────────────────────────────────


def gh_json(args: list[str]) -> Any:
    """Run a gh command and parse JSON output. Returns None on failure."""
    try:
        proc = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def gh_whoami() -> str | None:
    """Get the authenticated GitHub username via gh api user."""
    try:
        proc = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            check=False, capture_output=True, text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def gh_pr_snapshot(repo: str, pr_number: int) -> dict[str, Any] | None:
    return gh_json([
        "pr", "view", str(pr_number),
        "--repo", repo,
        "--json", "state,mergedAt,statusCheckRollup,headRefOid,title",
    ])


def gh_issue_comments(repo: str, pr_number: int, since: str | None) -> list[dict[str, Any]]:
    """Fetch issue (top-level) comments, optionally since a timestamp."""
    path = f"repos/{repo}/issues/{pr_number}/comments?per_page=100"
    if since:
        path += f"&since={since}"
    result = gh_json(["api", path, "--paginate"])
    return result if isinstance(result, list) else []


def gh_review_comments(repo: str, pr_number: int, since: str | None) -> list[dict[str, Any]]:
    """Fetch review (inline) comments, optionally since a timestamp."""
    path = f"repos/{repo}/pulls/{pr_number}/comments?per_page=100"
    if since:
        path += f"&since={since}"
    result = gh_json(["api", path, "--paginate"])
    return result if isinstance(result, list) else []


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


def classify_comment(
    comment: dict[str, Any],
    author_map: dict[int, str],
    my_login: str,
) -> str:
    """Classify a comment as new / reply_to_me / reply_to_other."""
    reply_id = comment.get("in_reply_to_id")
    if reply_id is None:
        return "new"
    parent_author = author_map.get(reply_id, "")
    return "reply_to_me" if parent_author == my_login else "reply_to_other"


# ── Discovery ──────────────────────────────────────────────────────


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


# ── Event emission ─────────────────────────────────────────────────


def post_task_event(**fields: object) -> bool:
    """POST a task event. Returns True on success, False on any failure."""
    import httpx

    base = os.environ["OMNIGENT_SERVER_URL"].rstrip("/")
    host_id = os.environ["OMNIGENT_HOST_ID"]
    try:
        resp = httpx.post(
            f"{base}/v1/task-events",
            headers={"X-Omnigent-Host-Id": host_id},
            json=fields,
            timeout=30.0,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def emit_transition(
    *,
    plugin_name: str,
    repo: str,
    pr_number: int,
    event_type: str,
    title: str,
    source_offset: str,
    payload: dict[str, Any],
) -> bool:
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
    return post_task_event(**fields)


# ── Main ───────────────────────────────────────────────────────────


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

    my_login = gh_whoami()

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
        if not isinstance(previous, dict):
            previous = {}
        merged_at = snapshot.get("mergedAt")
        checks = checks_conclusion(snapshot)
        context = target.get("context") if isinstance(target.get("context"), dict) else {}

        # ── Merge (with GC) ──
        if merged_at and not previous.get("mergedAt"):
            ok = emit_transition(
                plugin_name=plugin_name,
                repo=str(repo),
                pr_number=int(pr_number),
                event_type="github.pr.merged",
                title=f"PR #{pr_number} merged in {repo}",
                source_offset="merged",
                payload={
                    "repo": repo,
                    "pr_number": pr_number,
                    "merged_at": merged_at,
                    "context": context,
                },
            )
            if ok:
                state.pop(key, None)
                continue
            else:
                continue  # retry next tick, state unchanged

        # ── Checks failed ──
        if checks == "FAILURE" and previous.get("checks") != "FAILURE":
            ok = emit_transition(
                plugin_name=plugin_name,
                repo=str(repo),
                pr_number=int(pr_number),
                event_type="github.pr.checks_failed",
                title=f"PR #{pr_number} checks failed in {repo}",
                source_offset="checks_failed",
                payload={"repo": repo, "pr_number": pr_number, "context": context},
            )
            if not ok:
                continue  # retry next tick, state unchanged

        # (checks_passed removed — not fired)

        # ── Comments ──
        if my_login:
            since = previous.get("last_comment_at")
            issue_comments = gh_issue_comments(str(repo), int(pr_number), since)
            review_comments = gh_review_comments(str(repo), int(pr_number), since)

            # Build author map for reply classification (review comments only)
            author_map: dict[int, str] = {
                c["id"]: c.get("user", {}).get("login", "")
                for c in review_comments
                if isinstance(c.get("id"), int)
            }

            seen: set[int] = set(previous.get("seen_comment_ids", []))
            new_last_comment_at = previous.get("last_comment_at")

            # Process issue comments first, then review comments, oldest-first
            all_comments: list[tuple[str, dict[str, Any]]] = (
                [("issue", c) for c in issue_comments]
                + [("review", c) for c in review_comments]
            )
            all_comments.sort(key=lambda x: x[1].get("created_at", ""))

            for source_type, comment in all_comments:
                cid = comment.get("id")
                if not isinstance(cid, int) or cid in seen:
                    continue
                author = comment.get("user", {}).get("login", "")
                body = comment.get("body", "") or ""
                created_at = comment.get("created_at", "")
                comment_type = classify_comment(comment, author_map, my_login)
                ok = emit_transition(
                    plugin_name=plugin_name,
                    repo=str(repo),
                    pr_number=int(pr_number),
                    event_type=f"github.pr.comment.{comment_type}",
                    title=f"PR #{pr_number} {comment_type.replace('_', ' ')} by {author} in {repo}",
                    source_offset=f"comment:{cid}",
                    payload={
                        "repo": repo,
                        "pr_number": pr_number,
                        "comment_id": cid,
                        "author": author,
                        "body_preview": body[:200],
                        "comment_type": comment_type,
                        "source": source_type,
                        "context": context,
                    },
                )
                if not ok:
                    break  # retry this PR's remaining comments next tick
                seen.add(cid)
                if created_at and (new_last_comment_at is None or created_at > new_last_comment_at):
                    new_last_comment_at = created_at

            state[key] = {
                "mergedAt": merged_at,
                "checks": checks,
                "headRefOid": snapshot.get("headRefOid"),
                "last_comment_at": new_last_comment_at,
                "seen_comment_ids": list(seen),
            }
        else:
            # Can't identify self — still write PR state, skip comments
            state[key] = {
                "mergedAt": merged_at,
                "checks": checks,
                "headRefOid": snapshot.get("headRefOid"),
                "last_comment_at": previous.get("last_comment_at"),
                "seen_comment_ids": previous.get("seen_comment_ids", []),
            }

    save_json(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
