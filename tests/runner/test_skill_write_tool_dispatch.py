from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.runner.tool_dispatch import execute_tool


def _skill_md(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: Demo\n---\n\n{body}\n"


def _skill_hash(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in skill_dir.rglob("*") if item.is_file()):
        digest.update(path.relative_to(skill_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class _ServerClient:
    def __init__(self) -> None:
        self.puts: list[tuple[str, dict[str, object]]] = []
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def put(self, url: str, **kwargs: Any) -> _Response:
        self.puts.append((url, kwargs["json"]))
        return _Response({"results": [], "content_sha256": "updated"})

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self.posts.append((url, kwargs["json"]))
        return _Response({"results": [], "content_sha256": "created"})


@pytest.mark.asyncio
async def test_update_skill_uses_variant_recorded_by_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    skill_dir = home / ".claude" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(_skill_md("demo", "original"), encoding="utf-8")
    original_hash = _skill_hash(skill_dir)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    spec = SimpleNamespace(skills=[], skills_filter="all")
    server = _ServerClient()

    loaded = await execute_tool(
        tool_name="load_skill",
        arguments='{"name":"demo"}',
        agent_spec=spec,
        conversation_id="conv_skill_provenance",
        runner_workspace=tmp_path,
    )
    assert "original" in loaded
    skill_md.write_text(_skill_md("demo", "changed elsewhere"), encoding="utf-8")

    result = await execute_tool(
        tool_name="update_skill",
        arguments=json.dumps({"name": "demo", "files": {"SKILL.md": _skill_md("demo", "new")}}),
        server_client=server,  # type: ignore[arg-type]
        agent_spec=spec,
        conversation_id="conv_skill_provenance",
        runner_workspace=tmp_path,
    )

    assert json.loads(result)["content_sha256"] == "updated"
    assert server.puts[0][0] == f"/v1/skills/demo/variants/{original_hash}/files"


@pytest.mark.asyncio
async def test_update_skill_resolves_effective_variant_when_not_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    skill_dir = home / ".claude" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(_skill_md("demo", "original"), encoding="utf-8")
    expected_hash = _skill_hash(skill_dir)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    server = _ServerClient()

    await execute_tool(
        tool_name="update_skill",
        arguments=json.dumps({"name": "demo", "files": {"SKILL.md": _skill_md("demo", "new")}}),
        server_client=server,  # type: ignore[arg-type]
        agent_spec=SimpleNamespace(skills=[], skills_filter="all"),
        conversation_id="conv_unread_skill",
        runner_workspace=tmp_path,
    )

    assert server.puts[0][0] == f"/v1/skills/demo/variants/{expected_hash}/files"


@pytest.mark.asyncio
async def test_write_skill_proxies_complete_tree_to_server() -> None:
    server = _ServerClient()
    files = {
        "SKILL.md": _skill_md("new-skill", "instructions"),
        "references/guide.md": "guide",
    }

    result = await execute_tool(
        tool_name="write_skill",
        arguments=json.dumps({"name": "new-skill", "files": files}),
        server_client=server,  # type: ignore[arg-type]
        agent_spec=SimpleNamespace(skills=[], skills_filter="all"),
        conversation_id="conv_write_skill",
    )

    assert json.loads(result)["content_sha256"] == "created"
    assert server.posts == [("/v1/skills/new-skill/files", {"files": files})]
