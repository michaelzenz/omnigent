#!/usr/bin/env python3
"""Timer plugin: reminder — fire a one-shot reminder event at ``fire_at``.

To make this recurring, uncomment the re-arm block at the bottom: it writes a
new future ``fire_at`` to ``config.yaml`` so the host fires again later.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import yaml

PLUGIN_DIR = Path(os.environ["OMNIGENT_PLUGIN_DIR"])
CONFIG_PATH = PLUGIN_DIR / "config.yaml"


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


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
    urllib.request.urlopen(req)


def main() -> None:
    cfg = load_config()
    fire_at = cfg.get("fire_at")
    if fire_at is None:
        return

    post_task_event(
        event_type="timer.reminder",
        title=cfg.get("title", "Timer reminder fired"),
        summary=f"timer:{PLUGIN_DIR.name} reminder fire_at:{int(fire_at)}",
        source=f"timer_plugin:{PLUGIN_DIR.name}",
        source_key=str(int(fire_at)),
        source_offset=1,
        payload={"plugin": PLUGIN_DIR.name, "fire_at": int(fire_at)},
    )

    # Recurring example: re-arm for 1 hour after the previous fire_at.
    # cfg["fire_at"] = int(fire_at) + 3600
    # CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))


if __name__ == "__main__":
    main()
