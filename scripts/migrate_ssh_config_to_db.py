"""One-time migration of SSH settings from config.yaml to server storage."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from omnigent.db.utils import _run_migrations
from omnigent.entities import SshConnectionProfile
from omnigent.stores.ssh_host_installation_store import SshHostInstallationStore
from omnigent.version import VERSION


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--database-uri", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    raw = yaml.safe_load(args.config.read_text()) or {}
    if not isinstance(raw, dict):
        raise SystemExit("config must contain a YAML mapping")

    connections = raw.get("ssh_connections") or []
    settings = raw.get("ssh_settings") or {}
    if not isinstance(connections, list) or not isinstance(settings, dict):
        raise SystemExit("SSH configuration has an invalid shape")

    profiles = [
        SshConnectionProfile(
            id=str(item["id"]),
            label=str(item["label"]),
            alias=str(item["alias"]),
            created_at=str(item["created_at"]),
            owner=str(item["owner"]) if item.get("owner") is not None else None,
        )
        for item in connections
        if isinstance(item, dict)
    ]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    database_url = make_url(args.database_uri)
    if not database_url.drivername.startswith("sqlite") or not database_url.database:
        raise SystemExit("this local migration utility requires a SQLite database URI")
    database_path = Path(database_url.database)
    database_backup = database_path.with_name(f"{database_path.name}.ssh-backup-{timestamp}")
    with (
        sqlite3.connect(database_path) as source,
        sqlite3.connect(database_backup) as destination,
    ):
        source.backup(destination)

    engine = create_engine(args.database_uri)
    try:
        _run_migrations(engine, args.database_uri)
    finally:
        engine.dispose()

    store = SshHostInstallationStore(args.database_uri)
    existing = {profile.id: profile for profile in store.profiles()}
    for profile in profiles:
        prior = existing.get(profile.id)
        if prior is not None and prior.alias != profile.alias:
            raise SystemExit(f"database alias mismatch for SSH profile {profile.id}")

    store.sync_connections(
        {profile.id: profile for profile in profiles},
        bundle_version=VERSION,
        owner="local",
    )
    package_index_url = settings.get("package_index_url")
    store.update_settings(
        package_index_url=str(package_index_url) if package_index_url else None,
        npm_registry_url=None,
        updated_by="manual-config-migration",
    )

    backup = args.config.with_name(f"{args.config.name}.ssh-backup-{timestamp}")
    shutil.copy2(args.config, backup)
    raw.pop("ssh_connections", None)
    raw.pop("ssh_settings", None)
    host = raw.get("host")
    if isinstance(host, dict):
        ssh = host.get("ssh")
        if isinstance(ssh, dict):
            ssh.pop("max_concurrent_commands", None)
            if not ssh:
                host.pop("ssh", None)
    args.config.write_text(yaml.safe_dump(raw, sort_keys=False))
    print(
        f"Migrated {len(profiles)} SSH profile(s); "
        f"backups: {database_backup}, {backup}"
    )


if __name__ == "__main__":
    main()
