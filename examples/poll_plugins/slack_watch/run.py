#!/usr/bin/env python3
"""Poll plugin: slack_watch — ingest inbound Slack messages as task events.

Polls the Slack Web API for new messages across channels, DMs, and MPIMs since
per-conversation watermarks, and posts a ``slack.message.received`` task event
for each new message from someone other than the token owner.

State is persisted in ``state.yaml`` alongside ``run.py``. See README.md for the
full design, state shape, and edit contract — READ IT BEFORE EDITING.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml

PLUGIN_DIR = Path(os.environ["OMNIGENT_PLUGIN_DIR"])
CONFIG_PATH = PLUGIN_DIR / "config.yaml"
STATE_PATH = PLUGIN_DIR / "state.yaml"

_SLACK_API = "https://slack.com/api"


# ── Config ──────────────────────────────────────────────────────────


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


def resolve_token(cfg: dict[str, Any]) -> str:
    token = os.environ.get("OMNIGENT_SLACK_TOKEN") or cfg.get("token")
    if not token:
        raise RuntimeError(
            "slack_watch: no Slack token — set OMNIGENT_SLACK_TOKEN or `token` in config.yaml"
        )
    return token


# ── State ───────────────────────────────────────────────────────────


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"conversations": {}, "threads": {}, "seen": []}
    data = yaml.safe_load(STATE_PATH.read_text()) or {}
    if not isinstance(data, dict):
        return {"conversations": {}, "threads": {}, "seen": []}
    data.setdefault("conversations", {})
    data.setdefault("threads", {})
    data.setdefault("seen", [])
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(yaml.safe_dump(state, sort_keys=False))


# ── Slack Web API ──────────────────────────────────────────────────


def slack_get(token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET https://slack.com/api/<method>?<params>. Raises on HTTP error."""
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{_SLACK_API}/{method}?{qs}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"slack {method} HTTP failed: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        err = payload.get("error") if isinstance(payload, dict) else "unknown"
        raise RuntimeError(f"slack {method} error: {err}")
    return payload


def post_task_event(**fields: object) -> None:
    base = os.environ["OMNIGENT_SERVER_URL"].rstrip("/")
    host_id = os.environ["OMNIGENT_HOST_ID"]
    body = json.dumps(fields).encode()
    req = urllib.request.Request(
        f"{base}/v1/task-events",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Omnigent-Host-Id": host_id,
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30)


# ── Helpers ────────────────────────────────────────────────────────


def conversation_kind(channel: dict[str, Any]) -> str:
    if channel.get("is_im"):
        return "im"
    if channel.get("is_mpim"):
        return "mpim"
    return "channel"


def updated_to_s(updated_ms: Any) -> float:
    """Slack channel `updated` is milliseconds; normalize to seconds."""
    if isinstance(updated_ms, (int, float)):
        return float(updated_ms) / 1000.0
    return 0.0


def ts_to_float(ts: str) -> float:
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def emit_message(
    *,
    plugin_name: str,
    channel_id: str,
    channel_name: str,
    kind: str,
    message: dict[str, Any],
    partner: str | None,
) -> None:
    ts = message.get("ts", "")
    text = message.get("text", "") or ""
    user = message.get("user")
    thread_ts = message.get("thread_ts")
    title = f"Slack {kind} message in {channel_name or channel_id}"
    if len(text) > 120:
        title += f": {text[:120]}…"
    elif text:
        title += f": {text}"
    payload: dict[str, Any] = {
        "channel_id": channel_id,
        "channel": channel_name,
        "kind": kind,
        "user": user,
        "text": text,
        "ts": ts,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if partner:
        payload["partner"] = partner
    post_task_event(
        event_type="slack.message.received",
        title=title,
        summary=f"slack:{kind}:{channel_id} ts:{ts}",
        source=f"poll_plugin:{plugin_name}",
        source_key=f"{channel_id}:{ts}",
        source_offset=1,
        payload=payload,
    )


# ── Main ────────────────────────────────────────────────────────────


def main() -> int:
    cfg = load_config()
    token = resolve_token(cfg)
    types = cfg.get("types", "public_channel,private_channel,im,mpim")
    limit = int(cfg.get("limit", 200))
    backfill_s = float(cfg.get("backfill_s", 60))
    ignore_bots = bool(cfg.get("ignore_bots", True))
    ignore_subtypes = set(cfg.get("ignore_subtypes") or [])
    seen_bound = int(cfg.get("seen_bound", 2000))
    plugin_name = os.environ.get("OMNIGENT_PLUGIN_NAME", PLUGIN_DIR.name)

    # Who am I? Filter out our own messages — we only ingest others' messages.
    auth = slack_get(token, "auth.test", {})
    self_user_id = auth.get("user_id")

    state = load_state()
    conversations: dict[str, Any] = state["conversations"]
    threads: dict[str, Any] = state["threads"]
    seen: list[str] = state["seen"]
    seen_set: set[str] = set(seen)
    now = time.time()

    # ── Discovery: first page of conversations.list, diff new IDs ──
    list_resp = slack_get(
        token,
        "conversations.list",
        {"types": types, "limit": limit},
    )
    for channel in list_resp.get("channels") or []:
        cid = channel.get("id")
        if not cid or cid in conversations:
            continue
        kind = conversation_kind(channel)
        conversations[cid] = {
            "name": channel.get("name") or "",
            "kind": kind,
            "updated": updated_to_s(channel.get("updated")),
            "watermark": now - backfill_s,
            "partner": channel.get("user") if kind == "im" else None,
        }

    # ── Per-conversation history + thread fan-out ──
    for cid, meta in conversations.items():
        ch_updated = meta.get("updated", 0.0)
        watermark = meta.get("watermark", 0.0)
        # Skip dormant conversations: no activity since our watermark.
        if ch_updated and ch_updated <= watermark:
            continue
        history = slack_get(
            token,
            "conversations.history",
            {"channel": cid, "oldest": f"{watermark:.6f}", "limit": limit},
        )
        messages = history.get("messages") or []
        # history returns newest-first; process oldest-first for stable watermarks.
        messages = sorted(messages, key=lambda m: ts_to_float(m.get("ts", "0")))
        newest = watermark
        for message in messages:
            mts = ts_to_float(message.get("ts", "0"))
            key = f"{cid}:{message.get('ts')}"
            if key in seen_set:
                if mts > newest:
                    newest = mts
                continue
            if message.get("user") == self_user_id:
                seen_set.add(key)
                if mts > newest:
                    newest = mts
                continue
            if ignore_bots and message.get("bot_id"):
                seen_set.add(key)
                if mts > newest:
                    newest = mts
                continue
            if message.get("subtype") in ignore_subtypes:
                seen_set.add(key)
                if mts > newest:
                    newest = mts
                continue
            with suppress(OSError):
                emit_message(
                    plugin_name=plugin_name,
                    channel_id=cid,
                    channel_name=meta.get("name", ""),
                    kind=meta.get("kind", "channel"),
                    message=message,
                    partner=meta.get("partner"),
                )
            seen_set.add(key)
            if mts > newest:
                newest = mts

            # Thread fan-out: only when this message is a thread root with a
            # newer reply (root has thread_ts == ts). Broadcast replies
            # (thread_ts != ts) are already surfaced as top-level history
            # entries, so we don't re-fetch their thread here.
            thread_ts = message.get("thread_ts")
            latest_reply = message.get("latest_reply")
            if thread_ts and latest_reply:
                tkey = f"{cid}:{thread_ts}"
                t_watermark = threads.get(tkey, {}).get("watermark", 0.0)
                if ts_to_float(latest_reply) > t_watermark and ts_to_float(
                    thread_ts
                ) == ts_to_float(message.get("ts", "")):
                    # This is a thread root; fetch its new replies.
                    _fetch_thread_replies(
                        token=token,
                        channel_id=cid,
                        thread_ts=thread_ts,
                        oldest=t_watermark,
                        limit=limit,
                        self_user_id=self_user_id,
                        ignore_bots=ignore_bots,
                        ignore_subtypes=ignore_subtypes,
                        seen_set=seen_set,
                        plugin_name=plugin_name,
                        channel_name=meta.get("name", ""),
                        kind=meta.get("kind", "channel"),
                        partner=meta.get("partner"),
                        threads=threads,
                    )
        if newest > watermark:
            meta["watermark"] = newest
        meta["updated"] = ch_updated or newest

    # Bound the dedup set.
    if len(seen_set) > seen_bound:
        state["seen"] = sorted(seen_set)[-seen_bound:]
    else:
        state["seen"] = list(seen_set)

    save_state(state)
    return 0


def _fetch_thread_replies(
    *,
    token: str,
    channel_id: str,
    thread_ts: str,
    oldest: float,
    limit: int,
    self_user_id: str | None,
    ignore_bots: bool,
    ignore_subtypes: set[str],
    seen_set: set[str],
    plugin_name: str,
    channel_name: str,
    kind: str,
    partner: str | None,
    threads: dict[str, Any],
) -> None:
    resp = slack_get(
        token,
        "conversations.replies",
        {"channel": channel_id, "ts": thread_ts, "oldest": f"{oldest:.6f}", "limit": limit},
    )
    replies = sorted(resp.get("messages") or [], key=lambda m: ts_to_float(m.get("ts", "0")))
    newest = oldest
    for message in replies:
        rts = ts_to_float(message.get("ts", "0"))
        key = f"{channel_id}:{message.get('ts')}"
        if key in seen_set:
            if rts > newest:
                newest = rts
            continue
        if message.get("user") == self_user_id:
            seen_set.add(key)
            if rts > newest:
                newest = rts
            continue
        if ignore_bots and message.get("bot_id"):
            seen_set.add(key)
            if rts > newest:
                newest = rts
            continue
        if message.get("subtype") in ignore_subtypes:
            seen_set.add(key)
            if rts > newest:
                newest = rts
            continue
        with suppress(OSError):
            emit_message(
                plugin_name=plugin_name,
                channel_id=channel_id,
                channel_name=channel_name,
                kind=kind,
                message=message,
                partner=partner,
            )
        seen_set.add(key)
        if rts > newest:
            newest = rts
    tkey = f"{channel_id}:{thread_ts}"
    threads[tkey] = {"watermark": newest}


if __name__ == "__main__":
    raise SystemExit(main())
