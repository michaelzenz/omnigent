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
        "--no-build-web", action="store_true",
        help="Skip building the web UI; use whatever is in omnigent/server/static/web-ui/",
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


def _find_electron_binary() -> str | None:
    """Find the Electron binary in the repo's node_modules."""
    candidates = [
        _REPO_ROOT / "node_modules" / ".pnpm",
    ]
    for pnpm_dir in candidates:
        if not pnpm_dir.is_dir():
            continue
        for entry in pnpm_dir.iterdir():
            if entry.name.startswith("electron@"):
                electron_app = entry / "node_modules" / "electron" / "dist"
                if sys.platform == "darwin":
                    binary = electron_app / "Electron.app" / "Contents" / "MacOS" / "Electron"
                elif sys.platform.startswith("linux"):
                    binary = electron_app / "electron"
                else:
                    binary = electron_app / "electron.exe"
                if binary.is_file():
                    return str(binary)
    return None


def _open_isolated_electron(server_url: str, instance_name: str) -> None:
    """Launch a separate Electron window with its own user-data-dir and settings."""
    electron_bin = _find_electron_binary()
    electron_app_dir = _REPO_ROOT / "web" / "electron"

    if not electron_bin:
        raise SystemExit("Electron binary not found in node_modules; run `pnpm install` first")
    if not electron_app_dir.is_dir():
        raise SystemExit(f"Electron app directory not found: {electron_app_dir}")

    # Use a per-instance user-data-dir so the experiment gets its own settings,
    # window state, and session storage — completely isolated from the main app.
    if sys.platform == "darwin":
        user_data_dir = Path.home() / "Library" / "Application Support" / f"Omnigent-{instance_name}"
    elif sys.platform.startswith("linux"):
        user_data_dir = Path.home() / ".config" / f"Omnigent-{instance_name}"
    else:
        user_data_dir = Path.home() / "AppData" / "Roaming" / f"Omnigent-{instance_name}"

    user_data_dir.mkdir(parents=True, exist_ok=True)
    settings_path = user_data_dir / "settings.json"
    settings_path.write_text(json.dumps({"server_url": server_url}, indent=2) + "\n")

    # Chromium switches with values must use --name=value here; otherwise Electron
    # treats the value as the app path.
    subprocess.Popen(
        [electron_bin, f"--user-data-dir={user_data_dir}", str(electron_app_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
        # Close the isolated Electron window by user-data-dir match.
        # The deep-link /close path only works for the main Electron instance.
        label = f"Omnigent-{args.name}"
        if sys.platform == "darwin":
            subprocess.run(
                ["pkill", "-f", label],
                capture_output=True,
            )
        elif sys.platform.startswith("linux"):
            subprocess.run(
                ["pkill", "-f", label],
                capture_output=True,
            )
        print(f"  Electron window closed.")

    print(f"\nInstance {args.name!r} is stopped. Files kept at:")
    print(f"  {instance_dir}")
    print(f"\nTo delete the files:  rm -rf {shlex.quote(str(instance_dir))}")


def _check_single_alembic_head() -> str:
    """Return the single Alembic head using the repository virtualenv."""
    result = subprocess.run(
        [
            str(_OMNIGENT_PYTHON),
            "-m",
            "alembic",
            "-c",
            str(_REPO_ROOT / "omnigent" / "db" / "alembic.ini"),
            "heads",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "Could not inspect Alembic heads:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    heads = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    if len(heads) != 1:
        raise SystemExit(
            f"Alembic has {len(heads)} heads — merge them before forking: {' '.join(heads)}"
        )
    return heads[0]


def _upgrade_db_to_head(db_path: Path) -> None:
    """Run alembic upgrade head on the forked DB to apply any new migrations.

    The source DB snapshot already has all tables from its own migrations.
    If the worktree has additional migrations (e.g. new feature tables),
    upgrade applies them. If the DB is already at head, this is a no-op.
    """
    _check_single_alembic_head()

    # Run alembic upgrade via the CLI, pointing at the forked DB.
    env = {
        **os.environ,
        # alembic.ini reads the DB URL from the environment or a hardcoded default.
        # Override to point at our forked DB.
        "OMNIGENT_DATABASE_URI": f"sqlite:///{db_path}",
    }
    result = subprocess.run(
        [
            str(_OMNIGENT_PYTHON),
            "-m",
            "alembic",
            "-c",
            str(_REPO_ROOT / "omnigent" / "db" / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "Database migration failed:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


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

        print("Running DB migrations to head...")
        _upgrade_db_to_head(dest_db)

        if not args.no_build_web:
            print("Building web UI...")
            vite_bin = _REPO_ROOT / "web" / "node_modules" / ".bin" / "vite"
            if not vite_bin.is_file():
                raise SystemExit("vite not found; run `pnpm install` first")
            build_result = subprocess.run(
                [str(vite_bin), "build"],
                cwd=str(_REPO_ROOT / "web"),
                capture_output=True,
                text=True,
            )
            if build_result.returncode != 0:
                raise SystemExit(
                    "Web UI build failed:\n"
                    f"{build_result.stderr.strip() or build_result.stdout.strip()}"
                )
            print("  Web UI built successfully.")

        print("Copying artifacts...")
        if source_artifacts.is_dir():
            shutil.copytree(source_artifacts, artifacts_dir)
        else:
            artifacts_dir.mkdir()

        inherited_pythonpath = os.environ.get("PYTHONPATH")
        env = {
            **os.environ,
            "OMNIGENT_DATA_DIR": str(instance_dir),
            "OMNIGENT_CONFIG_HOME": str(config_home),
            # Runner zygotes use Python -P, so cwd is intentionally absent from
            # sys.path. Put this worktree first instead of inheriting another
            # checkout's PYTHONPATH and silently running stale runner code.
            "PYTHONPATH": os.pathsep.join(
                part
                for part in (str(_REPO_ROOT), inherited_pythonpath)
                if part
            ),
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
            print("Opening an isolated Electron window...")
            _open_isolated_electron(server_url, args.name)

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
