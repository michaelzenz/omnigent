from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from omnigent.pi_local_config import resolve_usable_pi_local_config


def _write_models(agent_dir: Path) -> None:
    agent_dir.mkdir(parents=True)
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "databricks-claude": {
                        "baseUrl": "https://example.databricks.com/ai-gateway/anthropic",
                        "api": "anthropic-messages",
                        "apiKey": "!ucode auth token",
                        "models": [{"id": "system.ai.claude-sonnet-5"}],
                    },
                    "ambient-only": {
                        "baseUrl": "https://example.com/v1",
                        "api": "openai-completions",
                        "models": [{"id": "other-model"}],
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def test_resolves_only_providers_pi_reports_ready(monkeypatch, tmp_path: Path) -> None:
    agent_dir = tmp_path / ".pi" / "agent"
    _write_models(agent_dir)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout='{"status":"ready","provider":"databricks-claude"}',
        )

    monkeypatch.setattr("omnigent.pi_local_config.subprocess.run", fake_run)

    result = resolve_usable_pi_local_config(pi_path="/usr/bin/pi")

    assert result is not None
    assert result.agent_dir == agent_dir
    assert result.provider_ids == ("databricks-claude",)
    assert calls == [
        [
            "/usr/bin/pi",
            "auth",
            "check",
            "--provider",
            "databricks-claude",
            "--json",
            "--no-refresh",
        ]
    ]


def test_returns_none_when_pi_reports_auth_missing(monkeypatch, tmp_path: Path) -> None:
    agent_dir = tmp_path / ".pi" / "agent"
    _write_models(agent_dir)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.setattr(
        "omnigent.pi_local_config.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout='{"status":"missing"}',
        ),
    )

    assert resolve_usable_pi_local_config(pi_path="/usr/bin/pi") is None


def test_returns_none_for_models_without_self_contained_auth(monkeypatch, tmp_path: Path) -> None:
    agent_dir = tmp_path / ".pi" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "ambient": {
                        "baseUrl": "https://example.com/v1",
                        "models": [{"id": "model"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))

    assert resolve_usable_pi_local_config(pi_path="/usr/bin/pi") is None
