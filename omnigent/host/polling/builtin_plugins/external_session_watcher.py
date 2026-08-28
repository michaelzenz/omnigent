"""Built-in poll plugin: discover and track external sessions.

Scans configured directories for session transcript files (Codex, Cursor,
etc.), detects new sessions and transcript changes (including rewinds), and
posts events to the server. Uses incremental history hashes for robust
rewind detection.

State is persisted in ``state.yaml`` in the plugin directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import yaml

# When run by the poller infra, OMNIGENT_PLUGIN_DIR points to the per-plugin
# directory (<data_dir>/poll_plugins/<name>). When run standalone, fall back to
# deriving it from the data dir + plugin name.
_env_plugin_dir = os.environ.get("OMNIGENT_PLUGIN_DIR")
_plugin_name = os.environ.get("OMNIGENT_PLUGIN_NAME", "external_session_watcher")
if _env_plugin_dir:
    PLUGIN_DIR = Path(_env_plugin_dir)
else:
    PLUGIN_DIR = Path(os.path.expanduser("~/.omnigent/poll_plugins")) / _plugin_name
STATE_PATH = PLUGIN_DIR / "state.yaml"
CONFIG_PATH = PLUGIN_DIR / "config.yaml"


# ── Config ──────────────────────────────────────────────────────────


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


def expand_paths(paths: list[str]) -> list[Path]:
    return [Path(p).expanduser() for p in paths]


# ── State ───────────────────────────────────────────────────────────


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"sessions": {}, "rejected": []}
    return yaml.safe_load(STATE_PATH.read_text()) or {"sessions": {}, "rejected": []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(yaml.safe_dump(state, sort_keys=False))


# ── Transcript parsing ────────────────────────────────────────────


def parse_transcript(path: Path) -> list[str]:
    try:
        content = path.read_text(errors="replace")
    except OSError:
        return []
    lines = content.strip().split("\n")
    messages: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and "role" in obj:
                if current:
                    messages.append("\n".join(current))
                    current = []
                role = obj.get("role", "")
                content_val = obj.get("content") or obj.get("text") or ""
                current.append(f"{role}: {content_val}")
                continue
        except (json.JSONDecodeError, TypeError):
            pass
        current.append(stripped)

    if current:
        messages.append("\n".join(current))
    return messages


# ── Incremental history hashes ─────────────────────────────────────


def compute_history_hashes(messages: list[str]) -> list[str]:
    hashes: list[str] = []
    prev = ""
    for msg in messages:
        h = hashlib.sha256((prev + msg).encode()).hexdigest()[:16]
        hashes.append(h)
        prev = h
    return hashes


def find_divergence(
    stored_hashes: list[str],
    current_hashes: list[str],
) -> int:
    min_len = min(len(stored_hashes), len(current_hashes))
    for i in range(min_len):
        if stored_hashes[i] != current_hashes[i]:
            return i
    return min_len


# ── Server communication ──────────────────────────────────────────


def post_discovery(
    session_hint: str, path: str, tool: str, history_hash: str, snippet: str
) -> None:
    import httpx

    base = os.environ["OMNIGENT_SERVER_URL"].rstrip("/")
    host_id = os.environ.get("OMNIGENT_HOST_ID", "")
    headers = {}
    if host_id:
        headers["X-Omnigent-Host-Id"] = host_id
    resp = httpx.post(
        f"{base}/v1/task-events",
        headers=headers,
        json={
            "event_type": "external.session.discovered",
            "title": f"External session discovered: {session_hint}",
            "source": "external_session_watcher",
            "source_key": session_hint,
            "source_offset": f"host:{host_id}" if host_id else "",
            "payload": {
                "session_hint": session_hint,
                "path": path,
                "tool": tool,
                "history_hash": history_hash,
                "transcript_snippet": snippet,
            },
        },
        timeout=30.0,
    )
    resp.raise_for_status()


def post_update(
    session_hint: str,
    history_hash: str,
    transcript_delta: str,
    rewind_at: str | None = None,
) -> bool:
    import httpx

    base = os.environ["OMNIGENT_SERVER_URL"].rstrip("/")
    host_id = os.environ.get("OMNIGENT_HOST_ID", "")
    headers = {}
    if host_id:
        headers["X-Omnigent-Host-Id"] = host_id
    body: dict[str, Any] = {
        "session_hint": session_hint,
        "history_hash": history_hash,
        "transcript_delta": transcript_delta,
    }
    if rewind_at is not None:
        body["rewind_at"] = rewind_at
    resp = httpx.post(
        f"{base}/v1/external-session-watcher/update",
        headers=headers,
        json=body,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json().get("track", True)


# ── Main logic ─────────────────────────────────────────────────────


def session_hint_for(path: Path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()[:16]


def detect_tool(path: Path) -> str:
    parts = path.parts
    if ".codex" in parts:
        return "codex"
    if ".cursor" in parts:
        return "cursor"
    return "unknown"


def find_session_files(scan_dirs: list[Path]) -> list[Path]:
    results: list[Path] = []
    for d in scan_dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.suffix in (".jsonl", ".log", ".txt", ""):
                results.append(p)
    return results


def main() -> None:
    cfg = load_config()
    scan_dirs = expand_paths(cfg.get("scan_dirs", []))
    snippet_lines = cfg.get("snippet_lines", 50)
    recency_window_s = cfg.get("recency_window_s", 86400)
    sink_time_s = cfg.get("sink_time_s", 180)

    state = load_state()
    sessions: dict[str, Any] = state.setdefault("sessions", {})
    rejected: list[dict[str, Any]] = state.setdefault("rejected", [])
    rejected_hints = {r["hint"] for r in rejected}

    now = int(time.time())
    files = find_session_files(scan_dirs)

    for fpath in files:
        hint = session_hint_for(fpath)

        if hint in rejected_hints:
            continue

        try:
            mtime = int(fpath.stat().st_mtime)
        except OSError:
            continue
        if now - mtime > recency_window_s:
            continue

        if now - mtime < sink_time_s:
            continue

        messages = parse_transcript(fpath)
        if not messages:
            continue

        current_hashes = compute_history_hashes(messages)
        last_hash = current_hashes[-1] if current_hashes else ""

        known = sessions.get(hint)
        if known is None:
            snippet = "\n".join(messages[-snippet_lines:])
            try:
                post_discovery(
                    session_hint=hint,
                    path=str(fpath),
                    tool=detect_tool(fpath),
                    history_hash=last_hash,
                    snippet=snippet,
                )
            except Exception:
                continue
            sessions[hint] = {
                "last_history_hash": last_hash,
                "history_length": len(messages),
                "last_modified_at": mtime,
            }
        else:
            stored_hashes = compute_history_hashes(messages[: known.get("history_length", 0)])
            div = find_divergence(stored_hashes, current_hashes)

            if div >= len(current_hashes):
                continue

            if div == 0 and len(current_hashes) > 0:
                rewind_at = None
                delta_messages = messages
            elif div < len(current_hashes):
                rewind_at = stored_hashes[div - 1] if div > 0 else None
                delta_messages = messages[div:]
            else:
                rewind_at = None
                delta_messages = messages[known.get("history_length", 0) :]

            if not delta_messages:
                continue

            delta_text = "\n".join(delta_messages)
            try:
                track = post_update(
                    session_hint=hint,
                    history_hash=last_hash,
                    transcript_delta=delta_text,
                    rewind_at=rewind_at,
                )
            except Exception:
                continue

            if not track:
                rejected.append({"hint": hint, "rejected_at": now})
                rejected_hints.add(hint)
                sessions.pop(hint, None)
                continue

            sessions[hint] = {
                "last_history_hash": last_hash,
                "history_length": len(messages),
                "last_modified_at": mtime,
            }

    save_state(state)
