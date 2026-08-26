#!/usr/bin/env python3
"""Poll plugin: slack_watch — ingest inbound Slack messages as task events.

Polls Slack for new messages across channels, DMs, and MPIMs since
per-conversation watermarks, and posts a ``slack.message.received`` task event
for each new message from someone other than the authenticated user.

Instead of a persisted Slack token, this plugin drives a Slack MCP server
directly via the generic ``mcp`` SDK (stdio transport). The launch command
lives in ``config.yaml`` under ``mcp:`` — at Databricks ``dbexec`` resolves auth
via dbcert at runtime as the invoking user; elsewhere any MCP launch command
works. No Slack token is ever persisted or read by this code.

State is persisted in ``state.yaml`` alongside ``run.py``. See README.md for
the full design, state shape, and edit contract — READ IT BEFORE EDITING.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PLUGIN_DIR = Path(os.environ["OMNIGENT_PLUGIN_DIR"])
CONFIG_PATH = PLUGIN_DIR / "config.yaml"
STATE_PATH = PLUGIN_DIR / "state.yaml"


# ── Config ──────────────────────────────────────────────────────────


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


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


# ── Slack MCP client ───────────────────────────────────────────────


class SlackMcp:
    """Thin async wrapper over a Slack MCP ``slack_read_api_call`` tool.

    Each instance owns one MCP subprocess session for the lifetime of a tick.
    ``get`` maps 1:1 onto a Slack Web API GET: it calls
    ``slack_read_api_call`` with ``raw=True`` and returns the full parsed
    Slack response, raising on ``ok: false``.
    """

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result = await self._session.call_tool(
            "slack_read_api_call",
            {"endpoint": endpoint, "params": params or {}, "raw": True},
        )
        text = "".join(getattr(c, "text", "") or "" for c in (result.content or []))
        if not text:
            raise RuntimeError(f"slack {endpoint}: empty MCP response")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"slack {endpoint}: non-JSON MCP response: {text[:200]}") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            err = payload.get("error") if isinstance(payload, dict) else "unknown"
            raise RuntimeError(f"slack {endpoint} error: {err}")
        return payload


# ── Task event emit ────────────────────────────────────────────────


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


async def _fetch_thread_replies(
    *,
    slack: SlackMcp,
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
    resp = await slack.get(
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


async def _amain() -> int:
    cfg = load_config()
    types = cfg.get("types", "public_channel,private_channel,im,mpim")
    limit = int(cfg.get("limit", 200))
    backfill_s = float(cfg.get("backfill_s", 180))
    ignore_bots = bool(cfg.get("ignore_bots", True))
    ignore_subtypes = set(cfg.get("ignore_subtypes") or [])
    seen_bound = int(cfg.get("seen_bound", 2000))
    plugin_name = os.environ.get("OMNIGENT_PLUGIN_NAME", PLUGIN_DIR.name)

    mcfg = cfg.get("mcp")
    if not isinstance(mcfg, dict) or not mcfg.get("command"):
        raise RuntimeError(
            "slack_watch: no `mcp:` launch config in config.yaml (set mcp.command / mcp.args)"
        )
    params = StdioServerParameters(
        command=mcfg["command"],
        args=list(mcfg.get("args") or []),
        env=mcfg.get("env"),
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        slack = SlackMcp(session)

        # Who am I? Filter out our own messages — we only ingest others'.
        auth = await slack.get("auth.test", {})
        self_user_id = auth.get("user_id")

        state = load_state()
        conversations: dict[str, Any] = state["conversations"]
        threads: dict[str, Any] = state["threads"]
        seen: list[str] = state["seen"]
        seen_set: set[str] = set(seen)
        now = time.time()

        # ── Discovery: first page of conversations.list, diff new IDs ──
        list_resp = await slack.get(
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
            history = await slack.get(
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
                        await _fetch_thread_replies(
                            slack=slack,
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


async def _healthcheck() -> int:
    """Spawn the Slack MCP and call auth.test; exit 0 if healthy.

    Prints JSON on stdout: ``{"ok": bool, "detail": str}``. Used by the host
    for server-side plugin monitoring (later). Cheap and side-effect-free.
    """
    cfg = load_config()
    mcfg = cfg.get("mcp")
    if not isinstance(mcfg, dict) or not mcfg.get("command"):
        print(json.dumps({"ok": False, "detail": "no mcp launch config in config.yaml"}))
        return 1
    params = StdioServerParameters(
        command=mcfg["command"],
        args=list(mcfg.get("args") or []),
        env=mcfg.get("env"),
    )
    try:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            slack = SlackMcp(session)
            auth = await slack.get("auth.test", {})
            who = auth.get("user") or auth.get("user_id") or "unknown"
            print(json.dumps({"ok": True, "detail": f"auth.test ok as {who}"}))
            return 0
    except Exception as exc:  # noqa: BLE001 -- report any failure as unhealthy
        print(json.dumps({"ok": False, "detail": f"{type(exc).__name__}: {exc}"}))
        return 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--healthcheck":
        return asyncio.run(_healthcheck())
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
