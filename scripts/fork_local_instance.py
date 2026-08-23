#!/usr/bin/env python3
"""Fork the current local Omnigent state into a new running instance."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

_INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPO_ROOT = Path(__file__).resolve().parent.parent
_OMNIGENT_PYTHON = _REPO_ROOT / ".venv" / "bin" / "python"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="Instance name, e.g. experiment-a")
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop a forked instance (server + daemon + Electron window)",
    )
    parser.add_argument(
        "--source-data-dir",
        type=Path,
        default=Path(os.environ.get("OMNIGENT_DATA_DIR", "~/.omnigent")).expanduser(),
        help="Data directory to snapshot (default: $OMNIGENT_DATA_DIR or ~/.omnigent)",
    )
    parser.add_argument(
        "--instances-dir",
        type=Path,
        default=Path("~/.omnigent/instances").expanduser(),
        help="Parent directory for forked instances (default: ~/.omnigent/instances)",
    )
    parser.add_argument(
        "--config-home",
        type=Path,
        default=Path(os.environ.get("OMNIGENT_CONFIG_HOME", "~/.omnigent")).expanduser(),
        help="Shared config directory (default: $OMNIGENT_CONFIG_HOME or ~/.omnigent)",
    )
    parser.add_argument(
        "--no-open", action="store_true", help="Do not open an Electron window (fork mode)"
    )
    parser.add_argument(
        "--no-close-window",
        action="store_true",
        help="Do not close the Electron window (stop mode)",
    )
    return parser


def _run_cli(args: list[str], *, env: dict[str, str], capture: bool = False) -> str:
    result = subprocess.run(
        [str(_OMNIGENT_PYTHON), "-m", "omnigent.cli", *args],
        check=True,
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=capture,
    )
    return result.stdout if capture else ""


def _snapshot_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
        source_db.backup(destination_db)


def _open_deep_link(deep_link: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", deep_link], check=True)
    elif sys.platform.startswith("linux"):
        subprocess.run(["xdg-open", deep_link], check=True)
    elif os.name == "nt":
        os.startfile(deep_link)  # type: ignore[attr-defined]
    else:
        raise SystemExit(
            f"opening Electron is unsupported on platform {sys.platform!r}; use {deep_link}"
        )


def _server_url_to_deep_link(server_url: str, path: str) -> str:
    parsed = urlparse(server_url)
    if parsed.scheme != "http" or not parsed.netloc:
        raise SystemExit(f"cannot build an Electron deep link from {server_url!r}")
    return f"omnigent://{parsed.netloc}{path}"


def _parse_instance_env(instance_dir: Path) -> dict[str, str]:
    env_file = instance_dir / "instance.env"
    if not env_file.is_file():
        raise SystemExit(f"instance.env not found: {env_file}")
    result: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            # Values are written with shlex.quote; reverse with shlex.split,
            # which correctly unquotes a single shell-quoted token (the stdlib
            # has no shlex.unquote).
            parts = shlex.split(value)
            result[key] = parts[0] if parts else ""
    return result


def _stop_instance(args: argparse.Namespace) -> None:
    instances_dir = args.instances_dir.expanduser().resolve()
    instance_dir = instances_dir / args.name
    if not instance_dir.is_dir():
        raise SystemExit(f"instance does not exist: {instance_dir}")

    metadata = _parse_instance_env(instance_dir)
    server_url = metadata.get("OMNIGENT_SERVER_URL")
    data_dir = metadata.get("OMNIGENT_DATA_DIR", str(instance_dir))
    config_home = metadata.get("OMNIGENT_CONFIG_HOME", str(args.config_home.expanduser()))

    if not server_url:
        raise SystemExit("OMNIGENT_SERVER_URL missing from instance.env")

    env = {
        **os.environ,
        "OMNIGENT_DATA_DIR": data_dir,
        "OMNIGENT_CONFIG_HOME": config_home,
    }

    print(f"Stopping instance {args.name!r}...")
    _run_cli(["server", "stop"], env=env)
    print("  Server + daemon stopped.")

    if not args.no_close_window:
        print("  Closing Electron window...")
        _open_deep_link(_server_url_to_deep_link(server_url, "/close"))

    print(f"\nInstance {args.name!r} is stopped. Files kept at:")
    print(f"  {instance_dir}")
    print(f"\nTo delete the files:  rm -rf {shlex.quote(str(instance_dir))}")


def _stamp_db_to_head(db_path: Path) -> None:
    """Stamp a forked DB to the current Alembic head (tables already exist from snapshot)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_REPO_ROOT / "omnigent" / "db" / "alembic.ini"))
    sd = ScriptDirectory.from_config(cfg)
    head = sd.get_heads()
    if len(head) != 1:
        raise RuntimeError(
            f"Alembic has {len(head)} heads — run "
            f"  alembic -c omnigent/db/alembic.ini merge -m 'merge heads' {' '.join(head)}"
        )
    head_rev = head[0]

    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        if row and row[0] == head_rev:
            return  # already at head
        if row:
            conn.execute("UPDATE alembic_version SET version_num = ?", (head_rev,))
        else:
            conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (head_rev,))
        conn.commit()


def _fork_instance(args: argparse.Namespace) -> None:
    source_dir = args.source_data_dir.expanduser().resolve()
    instances_dir = args.instances_dir.expanduser().resolve()
    instance_dir = instances_dir / args.name
    config_home = args.config_home.expanduser().resolve()
    source_db = source_dir / "chat.db"
    config_path = config_home / "config.yaml"

    if not _OMNIGENT_PYTHON.is_file():
        raise SystemExit(
            f"Omnigent virtualenv does not exist: {_OMNIGENT_PYTHON}; run uv sync first"
        )
    if not source_db.is_file():
        raise SystemExit(f"source database does not exist: {source_db}")
    if not config_path.is_file():
        raise SystemExit(f"shared config does not exist: {config_path}")
    if instance_dir.exists():
        raise SystemExit(f"instance already exists: {instance_dir}")

    print(f"Forking {source_dir} -> {instance_dir}")
    instance_dir.mkdir(parents=True, mode=0o700)
    artifacts_dir = instance_dir / "artifacts"
    source_artifacts = source_dir / "artifacts"

    server_started = False
    try:
        print("Creating consistent SQLite snapshot...")
        dest_db = instance_dir / "chat.db"
        _snapshot_database(source_db, dest_db)

        print("Stamping DB to current Alembic head...")
        _stamp_db_to_head(dest_db)

        print("Copying artifacts...")
        if source_artifacts.is_dir():
            shutil.copytree(source_artifacts, artifacts_dir)
        else:
            artifacts_dir.mkdir()

        env = {
            **os.environ,
            "OMNIGENT_DATA_DIR": str(instance_dir),
            "OMNIGENT_CONFIG_HOME": str(config_home),
        }

        print("Starting isolated server...")
        _run_cli(["server", "--background"], env=env)
        server_started = True
        status = json.loads(_run_cli(["server", "status", "--json"], env=env, capture=True))
        server_url = status.get("url")
        if not status.get("running") or not isinstance(server_url, str):
            raise RuntimeError("background server did not report a running URL")

        print("Starting isolated host daemon...")
        _run_cli(["host", "--server", server_url, "--background"], env=env)

        metadata = {
            "OMNIGENT_INSTANCE_NAME": args.name,
            "OMNIGENT_DATA_DIR": str(instance_dir),
            "OMNIGENT_CONFIG_HOME": str(config_home),
            "OMNIGENT_SERVER_URL": server_url,
            "OMNIGENT_SOURCE_DATA_DIR": str(source_dir),
            "OMNIGENT_FORKED_AT": datetime.now(UTC).isoformat(),
        }
        (instance_dir / "instance.env").write_text(
            "".join(f"{key}={shlex.quote(value)}\n" for key, value in metadata.items())
        )

        if not args.no_open:
            print("Opening a new Electron window...")
            _open_deep_link(_server_url_to_deep_link(server_url, "/"))

        print(f"Instance {args.name!r} is running at {server_url}")
        print("Stop it with:")
        print(f"  {Path(__file__).name} --stop {args.name}")
    except Exception:
        if server_started:
            with suppress(subprocess.CalledProcessError):
                _run_cli(["server", "stop"], env=env)
        print(f"Instance files were kept for inspection at {instance_dir}", file=sys.stderr)
        raise
    except KeyboardInterrupt:
        if server_started:
            with suppress(subprocess.CalledProcessError):
                _run_cli(["server", "stop"], env=env)
        print(f"\nInterrupted — instance files kept at {instance_dir}", file=sys.stderr)
        raise


def main() -> None:
    args = _parser().parse_args()
    if not _INSTANCE_NAME_RE.fullmatch(args.name):
        raise SystemExit(
            "name must start with an alphanumeric and contain only letters, numbers, ., _, or -"
        )
    if args.stop:
        _stop_instance(args)
    else:
        _fork_instance(args)


if __name__ == "__main__":
    main()
