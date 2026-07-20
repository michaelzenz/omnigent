"""Tests for Codex poller configuration."""

from __future__ import annotations

import pytest

from omnigent.host.polling.pollers.codex_config import (
    codex_ambient_sync_enabled,
    load_codex_poller_config,
)


def test_codex_poller_config_reads_polling_section(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIGENT_CODEX_AMBIENT_SYNC", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "host:\n  polling:\n    codex:\n      enabled: false\n      interval_s: 7.5\n",
        encoding="utf-8",
    )
    config = load_codex_poller_config(config_path)
    assert config.enabled is False
    assert config.interval_s == 7.5
    assert codex_ambient_sync_enabled(config_path) is False


def test_codex_poller_config_falls_back_to_legacy_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIGENT_CODEX_AMBIENT_SYNC", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("host:\n  codex_ambient_sync: false\n", encoding="utf-8")
    assert codex_ambient_sync_enabled(config_path) is False


def test_codex_poller_config_env_overrides_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("host:\n  polling:\n    codex:\n      enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("OMNIGENT_CODEX_AMBIENT_SYNC", "0")
    assert codex_ambient_sync_enabled(config_path) is False
