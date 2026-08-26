#!/usr/bin/env python3
"""Fork the current local Omnigent state into a new running instance."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

_INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPO_ROOT = Path(__file__).resolve().parent.parent
_OMNIGENT_PYTHON = _REPO_ROOT / ".venv" / "bin" / "python"
_WEB_INSTALL_ARGS = ["install", "--frozen-lockfile", "--shamefully-hoist"]


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
        "--no-build-web",
        action="store_true",
        help="Skip building the web UI; use whatever is in omnigent/server/static/web-ui/",
    )
    parser.add_argument(
        "--npm-registry",
        help="Registry to use if web dependencies need to be installed",
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


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _log_tail(path: Path, lines: int = 80) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(Electron log is unavailable)"


def _electron_user_data_dir(instance_name: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / f"Omnigent-{instance_name}"
    if sys.platform.startswith("linux"):
        return Path.home() / ".config" / f"Omnigent-{instance_name}"
    return Path.home() / "AppData" / "Roaming" / f"Omnigent-{instance_name}"


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _open_isolated_electron(
    server_url: str, instance_name: str, instance_dir: Path
) -> tuple[subprocess.Popen[bytes], Path]:
    """Launch Electron and fail if its SPA does not render successfully."""
    electron_bin = _find_electron_binary()
    electron_app_dir = _REPO_ROOT / "web" / "electron"
    node_bin = shutil.which("node")
    smoke_script = _REPO_ROOT / "scripts" / "check_electron_renderer.mjs"

    if not electron_bin:
        raise RuntimeError("Electron binary not found; run `pnpm install --shamefully-hoist`")
    if not node_bin:
        raise RuntimeError("Node.js not found; install the repository development prerequisites")
    if not electron_app_dir.is_dir():
        raise RuntimeError(f"Electron app directory not found: {electron_app_dir}")
    if not smoke_script.is_file():
        raise RuntimeError(f"Electron renderer smoke check not found: {smoke_script}")

    # Keep settings, window state, and session storage isolated from the main app.
    user_data_dir = _electron_user_data_dir(instance_name)

    user_data_dir.mkdir(parents=True, exist_ok=True)
    settings_path = user_data_dir / "settings.json"
    settings_path.write_text(json.dumps({"server_url": server_url}, indent=2) + "\n")

    log_dir = instance_dir / "logs" / "electron"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    log_path = log_dir / f"electron-{stamp}.log"
    debug_port = _free_loopback_port()

    # Chromium switches with values must use --name=value here; otherwise Electron
    # treats the value as the app path.
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [
                electron_bin,
                "--enable-logging=stderr",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={debug_port}",
                f"--user-data-dir={user_data_dir}",
                str(electron_app_dir),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    try:
        try:
            result = subprocess.run(
                [node_bin, str(smoke_script), server_url, str(debug_port)],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Electron renderer smoke check timed out. Log: {log_path}\n{_log_tail(log_path)}"
            ) from exc

        if result.returncode != 0:
            detail = (
                result.stderr.strip() or result.stdout.strip() or "renderer did not become ready"
            )
            raise RuntimeError(
                f"Electron renderer failed: {detail}\nLog: {log_path}\n{_log_tail(log_path)}"
            )
    finally:
        # Never leave the temporary CDP endpoint alive, including on Ctrl-C.
        if process.poll() is None:
            _terminate_process(process)

    # Relaunch the verified build without remote debugging.
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [
                electron_bin,
                "--enable-logging=stderr",
                f"--user-data-dir={user_data_dir}",
                str(electron_app_dir),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    try:
        time.sleep(0.5)
        if process.poll() is not None:
            raise RuntimeError(
                "Electron exited after renderer verification. "
                f"Log: {log_path}\n{_log_tail(log_path)}"
            )
    except BaseException:
        if process.poll() is None:
            _terminate_process(process)
        raise

    return process, log_path


def _close_isolated_electron(instance_name: str, pid: int | None = None) -> None:
    if sys.platform.startswith("win"):
        if pid is not None:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
            )
        return
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        return

    user_data_arg = f"--user-data-dir={_electron_user_data_dir(instance_name)}"
    pattern = re.escape(user_data_arg) + "([[:space:]]|$)"
    subprocess.run(["pkill", "-TERM", "-f", pattern], capture_output=True)
    for _ in range(50):
        found = subprocess.run(["pgrep", "-f", pattern], capture_output=True)
        if found.returncode != 0:
            return
        time.sleep(0.1)
    subprocess.run(["pkill", "-KILL", "-f", pattern], capture_output=True)


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
    electron_pid_text = metadata.get("OMNIGENT_ELECTRON_PID")
    electron_pid = (
        int(electron_pid_text) if electron_pid_text and electron_pid_text.isdigit() else None
    )

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
        # The deep-link /close path only works for the main Electron instance.
        _close_isolated_electron(args.name, electron_pid)
        print("  Electron window closed.")

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
        raise RuntimeError(
            f"Could not inspect Alembic heads:\n{result.stderr.strip() or result.stdout.strip()}"
        )
    heads = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    if len(heads) != 1:
        raise RuntimeError(
            f"Alembic has {len(heads)} heads — merge them before forking: {' '.join(heads)}"
        )
    return heads[0]


def _check_web_dependencies() -> Path:
    vite_bin = _REPO_ROOT / "web" / "node_modules" / ".bin" / "vite"
    node_bin = shutil.which("node")
    install_hint = f"pnpm {' '.join(_WEB_INSTALL_ARGS)}"
    if not vite_bin.is_file() or not node_bin:
        raise RuntimeError(f"Web dependencies are missing; run `{install_hint}`")

    probe = subprocess.run(
        [
            node_bin,
            "-e",
            "Promise.all([import('@radix-ui/react-dialog'), "
            "import('@radix-ui/react-slot'), import('react-dom/client')])",
        ],
        cwd=_REPO_ROOT / "web",
        capture_output=True,
        text=True,
        timeout=15,
    )
    if probe.returncode != 0:
        detail = probe.stderr.strip() or probe.stdout.strip()
        raise RuntimeError(f"Web dependencies are incomplete; run `{install_hint}`\n{detail}")
    return vite_bin


def _install_web_dependencies(registry: str | None = None) -> None:
    pnpm_bin = shutil.which("pnpm")
    if not pnpm_bin:
        raise RuntimeError("pnpm is unavailable; install the repository development prerequisites")
    env = {**os.environ, "CI": "true"}
    if registry:
        env["COREPACK_NPM_REGISTRY"] = registry
        env["npm_config_registry"] = registry
    result = subprocess.run(
        [pnpm_bin, *_WEB_INSTALL_ARGS],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Web dependency installation failed:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def _prepare_web_dependencies(registry: str | None = None) -> Path:
    try:
        return _check_web_dependencies()
    except RuntimeError as initial_error:
        print(f"  {initial_error}")
        if registry:
            print(f"Installing web dependencies through {registry}...")
        else:
            print("Installing web dependencies through the configured package registry...")
        _install_web_dependencies(registry)
        return _check_web_dependencies()


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
        raise RuntimeError(
            f"Database migration failed:\n{result.stderr.strip() or result.stdout.strip()}"
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
            print("Checking web dependencies...")
            vite_bin = _prepare_web_dependencies(args.npm_registry)
            print("Building web UI...")
            build_result = subprocess.run(
                [str(vite_bin), "build"],
                cwd=str(_REPO_ROOT / "web"),
                capture_output=True,
                text=True,
            )
            if build_result.returncode != 0:
                raise RuntimeError(
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
                part for part in (str(_REPO_ROOT), inherited_pythonpath) if part
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

        if not args.no_open:
            print("Opening and verifying an isolated Electron window...")
            electron_process, electron_log = _open_isolated_electron(
                server_url, args.name, instance_dir
            )
            metadata["OMNIGENT_ELECTRON_PID"] = str(electron_process.pid)
            metadata["OMNIGENT_ELECTRON_LOG"] = str(electron_log)
            print("  Electron renderer is ready.")

        (instance_dir / "instance.env").write_text(
            "".join(f"{key}={shlex.quote(value)}\n" for key, value in metadata.items())
        )

        print(f"Instance {args.name!r} is running at {server_url}")
        print("Stop it with:")
        print(f"  {Path(__file__).name} --stop {args.name}")
    except Exception:
        if not args.no_open:
            _close_isolated_electron(args.name)
        if server_started:
            with suppress(subprocess.CalledProcessError):
                _run_cli(["server", "stop"], env=env)
        print(f"Instance files were kept for inspection at {instance_dir}", file=sys.stderr)
        raise
    except KeyboardInterrupt:
        if not args.no_open:
            _close_isolated_electron(args.name)
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
