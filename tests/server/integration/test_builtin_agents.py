"""Integration tests for ``GET /v1/agents`` (built-in agent discovery).

The endpoint is the read-only successor to the removed
``GET /api/agents`` list and the source the new-session picker uses to
discover bindable built-in agents (designs/BUILTIN_AGENTS.md). The
``session_id IS NULL`` exclusion of session-scoped agents lives in
``agent_store.list()`` and is covered by
``tests/stores/test_agent_store.py``; these tests cover the endpoint
wiring and response envelope.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from omnigent.db.utils import builtin_agent_id
from omnigent.errors import OmnigentError
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.routes.builtin_agents import create_builtin_agents_router
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from tests.server.helpers import build_agent_bundle

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def agent_store(db_uri: str) -> SqlAlchemyAgentStore:
    """Agent store backed by the shared test SQLite db."""
    return SqlAlchemyAgentStore(db_uri)


@pytest.fixture()
def artifact_store(tmp_path: Path) -> LocalArtifactStore:
    """Artifact store for agent bundles, so tests can register a
    built-in agent with a real, loadable bundle."""
    return LocalArtifactStore(str(tmp_path / "artifacts"))


@pytest.fixture()
def agent_cache(artifact_store: LocalArtifactStore, tmp_path: Path) -> AgentCache:
    """Spec cache reading bundles from the test ``artifact_store``."""
    return AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache")


def _register_builtin_agent(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    *,
    agent_id: str,
    name: str,
    bundle: bytes,
    description: str | None = None,
    auto_select_enabled: bool | None = None,
) -> None:
    """Store a bundle and register a built-in (``session_id IS NULL``)
    agent pointing at it, mirroring the server's startup seeding.

    :param agent_store: Store the agent row is written to.
    :param artifact_store: Store the bundle bytes are written to.
    :param agent_id: Agent id, e.g. ``"12c8c7631b209d1027416b4bf7604999"``.
    :param name: Agent name, e.g. ``"codex-native-ui"``.
    :param bundle: Gzipped agent bundle bytes from
        :func:`build_agent_bundle`.
    :param description: Optional free-text description; the catalog
        surfaces it as the picker label.
    """
    bundle_key = f"{agent_id}/{hashlib.sha256(bundle).hexdigest()}"
    artifact_store.put(bundle_key, bundle)
    agent_store.create(
        agent_id,
        name,
        bundle_key,
        description=description,
        auto_select_enabled=auto_select_enabled,
    )


@pytest.fixture()
def agents_app(
    agent_store: SqlAlchemyAgentStore,
    agent_cache: AgentCache,
    artifact_store: LocalArtifactStore,
) -> FastAPI:
    """Minimal app mounting only the built-in agents router at ``/v1``."""
    app = FastAPI()

    @app.exception_handler(OmnigentError)
    async def _handle_omnigent_error(_request: Request, exc: OmnigentError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(
        create_builtin_agents_router(agent_store, agent_cache, artifact_store),
        prefix="/v1",
    )
    return app


@pytest_asyncio.fixture()
async def agents_client(agents_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client wired to the built-in-agents app."""
    transport = httpx.ASGITransport(app=agents_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_list_builtin_agents_returns_registered_templates(
    agent_store: SqlAlchemyAgentStore,
    agents_client: httpx.AsyncClient,
) -> None:
    """
    ``GET /v1/agents`` surfaces built-in agents registered in the store,
    with their id and name, inside the ``PaginatedList`` envelope.

    This is what the new-session picker reads; if registered built-ins
    don't appear, the picker is empty and no session can be created
    against a built-in. ``mcp_servers`` is empty here because the test
    agents have no real bundle (the spec load fails gracefully).
    """
    agent_store.create(
        "2d2cd1e48ffdf8a4c7195e954e2e912c",
        "claude-native-ui",
        "2d2cd1e48ffdf8a4c7195e954e2e912c/bundle",
    )
    agent_store.create(
        "01bda0a6f702a638bbee8d871441e659",
        "research-agent",
        "01bda0a6f702a638bbee8d871441e659/bundle",
    )

    resp = await agents_client.get("/v1/agents")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {a["id"] for a in body["data"]} == {
        "2d2cd1e48ffdf8a4c7195e954e2e912c",
        "01bda0a6f702a638bbee8d871441e659",
    }
    assert {a["name"] for a in body["data"]} == {"claude-native-ui", "research-agent"}
    assert body["has_more"] is False
    # No loadable bundle → harness degrades to None rather than failing
    # the list. A non-None value here would mean the route invented a
    # harness for an unreadable spec.
    assert all(a["harness"] is None for a in body["data"])
    # Same degradation for skills: an unreadable spec yields an empty
    # list, not an error and not invented entries.
    assert all(a["skills"] == [] for a in body["data"])


@pytest.mark.parametrize(
    "harness",
    ["codex", "claude-sdk"],
)
async def test_list_builtin_agents_exposes_harness_from_spec(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    agents_client: httpx.AsyncClient,
    harness: str,
) -> None:
    """
    ``GET /v1/agents`` reports each agent's ``harness`` from its spec's
    ``executor.config.harness``.

    The Web UI Add Agent picker uses this to recognise an agent's kind
    (Codex vs Claude) instead of hardcoding by name slug — a
    custom-registered Codex agent must be identifiable as Codex even
    when its name isn't ``codex-native-ui``. The value must reflect the
    actual spec, so the parametrize proves it isn't a hardcoded
    constant.
    """
    bundle = build_agent_bundle(
        name="custom-reviewer",
        executor={"type": "omnigent", "config": {"harness": harness}},
    )
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id="67914bd6ac8bd25239a14ef060e99e70",
        name="custom-reviewer",
        bundle=bundle,
    )

    resp = await agents_client.get("/v1/agents")

    assert resp.status_code == 200, resp.text
    entry = next(a for a in resp.json()["data"] if a["id"] == "67914bd6ac8bd25239a14ef060e99e70")
    # Proves the spec's harness traversed the load → AgentObject path.
    # A None here means the bundle failed to load; a different value
    # means the route read the wrong spec field.
    assert entry["harness"] == harness


async def test_list_builtin_agents_exposes_declared_terminals_from_spec(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    agents_client: httpx.AsyncClient,
) -> None:
    """
    ``GET /v1/agents`` reports each agent's declared ``terminals:`` names.

    The Web UI gates its "new terminal" affordance on this list — a
    dropped field hides creation from every agent with terminal
    access; an invented entry offers creation the server's gate will
    then reject.
    """
    bundle = build_agent_bundle(
        name="terminal-agent",
        terminals={"shell": {"command": "bash"}, "py": {"command": "python3"}},
    )
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id="5668ffcb1258cd047cfd400e190ed38c",
        name="terminal-agent",
        bundle=bundle,
    )

    resp = await agents_client.get("/v1/agents")

    assert resp.status_code == 200, resp.text
    entry = next(a for a in resp.json()["data"] if a["id"] == "5668ffcb1258cd047cfd400e190ed38c")
    # Both declared names in spec order — proves the spec's terminals
    # block traversed the load → AgentObject path verbatim.
    assert entry["terminals"] == ["shell", "py"]


async def test_list_builtin_agents_exposes_bundled_skills_from_spec(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    agents_client: httpx.AsyncClient,
) -> None:
    """
    ``GET /v1/agents`` reports each agent's bundled skills (name +
    description) from its spec's ``skills/<dir>/SKILL.md`` entries.

    The Web UI's new-session composer builds its "/" suggestions menu
    from this list — before a session exists there is no runner to
    merge host-discovered skills, so the bundled set is the only
    source. Missing entries here mean the landing menu is empty and a
    first-message skill invocation falls back to plain text. The skill
    ``content`` must NOT be exposed: it can be large and is only
    loaded runner-side at invocation time.
    """
    bundle = build_agent_bundle(
        name="skilled-agent",
        skills=[
            {
                "name": "review-pr",
                "description": "Review a pull request",
                "content": "Fetch the PR and review it.",
            },
            {
                "name": "triage",
                "description": "Triage issues",
                "content": "Ask one question.",
            },
        ],
    )
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id="66614758344e352ba3d265401d826803",
        name="skilled-agent",
        bundle=bundle,
    )

    resp = await agents_client.get("/v1/agents")

    assert resp.status_code == 200, resp.text
    entry = next(a for a in resp.json()["data"] if a["id"] == "66614758344e352ba3d265401d826803")
    # Both bundled skills traversed the load → AgentObject path with the
    # exact name + description the composer menu renders. A missing or
    # renamed entry means the landing "/" menu regressed to empty.
    assert entry["skills"] == [
        {"name": "review-pr", "description": "Review a pull request"},
        {"name": "triage", "description": "Triage issues"},
    ]
    # SkillSummary is the safe subset — the SKILL.md body must not leak
    # into the catalog payload.
    assert all("content" not in s for s in entry["skills"])


async def test_catalog_keeps_custom_agent_distinct_from_builtin_claude_and_codex(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    agents_client: httpx.AsyncClient,
) -> None:
    """
    A custom YAML agent registered as a built-in appears in the catalog
    alongside the Claude and Codex built-ins and stays distinguishable by
    name + harness, with its description surfaced as the picker label.

    This is the custom-agent value prop: bringing your own YAML agent
    (here ``databricks-coding-agent`` on the ``openai-agents`` harness)
    must not collapse into the built-ins. The Add Agent picker keys the
    glyph off harness and the label off name + description, so a custom
    entry that reported a built-in's harness or dropped its name would be
    mis-badged or indistinguishable from Claude/Codex. Registering all
    three in one list and asserting per-id fields proves the route keeps
    them separate rather than, say, reading one shared spec.
    """
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id="3a9725fd4de1720e83e53a632da41da8",
        name="claude-native-ui",
        bundle=build_agent_bundle(
            name="claude-native-ui",
            executor={"type": "omnigent", "config": {"harness": "claude-sdk"}},
        ),
    )
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id="12c8c7631b209d1027416b4bf7604999",
        name="codex-native-ui",
        bundle=build_agent_bundle(
            name="codex-native-ui",
            executor={"type": "omnigent", "config": {"harness": "codex"}},
        ),
    )
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id="f157d3110ddee6bc0f8e1b893270c8a6",
        name="databricks-coding-agent",
        description="Custom coding agent",
        bundle=build_agent_bundle(
            name="databricks-coding-agent",
            executor={"type": "omnigent", "config": {"harness": "openai-agents"}},
        ),
    )

    resp = await agents_client.get("/v1/agents")

    assert resp.status_code == 200, resp.text
    by_id = {a["id"]: a for a in resp.json()["data"]}
    # All three discoverable in the one catalog the picker reads.
    assert by_id.keys() >= {
        "3a9725fd4de1720e83e53a632da41da8",
        "12c8c7631b209d1027416b4bf7604999",
        "f157d3110ddee6bc0f8e1b893270c8a6",
    }
    # Each carries its own harness — the custom agent is neither the
    # Claude nor the Codex kind. A shared/wrong value here is exactly the
    # mis-badging this contract guards against.
    assert by_id["3a9725fd4de1720e83e53a632da41da8"]["harness"] == "claude-sdk"
    assert by_id["12c8c7631b209d1027416b4bf7604999"]["harness"] == "codex"
    assert by_id["f157d3110ddee6bc0f8e1b893270c8a6"]["harness"] == "openai-agents"
    # The custom agent keeps its registered name and description (the
    # picker's label), distinct from both built-ins.
    assert by_id["f157d3110ddee6bc0f8e1b893270c8a6"]["name"] == "databricks-coding-agent"
    assert by_id["f157d3110ddee6bc0f8e1b893270c8a6"]["description"] == "Custom coding agent"


async def test_list_builtin_agents_empty_when_none_registered(
    agents_client: httpx.AsyncClient,
) -> None:
    """
    With no agents registered, ``GET /v1/agents`` returns an empty list
    (not an error) so the picker can render a clean "no agents" state.
    """
    resp = await agents_client.get("/v1/agents")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    assert body["has_more"] is False


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AgentObject exposes no availability / unavailable_reason fields "
        "yet. The Add Agent picker can't grey out an agent that "
        "can't be launched in the current environment, nor explain why. "
        "Flips to a failing XPASS when the catalog grows the metadata — "
        "promote this to a real contract test then."
    ),
)
async def test_catalog_entry_exposes_availability_and_reason(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    agents_client: httpx.AsyncClient,
) -> None:
    """
    Each ``GET /v1/agents`` entry should report whether it is launchable
    plus a reason when it is not.

    Discoverability is only half the Add Agent contract: the picker also
    needs to know which catalog entries it may actually launch for this
    user/environment/session and why a disabled one is disabled. The two
    field names below are the proposed wire contract — strict xfail so the
    suite trips the moment the schema gains them and forces this test to
    be turned into a positive assertion.
    """
    bundle = build_agent_bundle(
        name="codex-reviewer",
        executor={"type": "omnigent", "config": {"harness": "codex"}},
    )
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id="77b35426aef3c1495c2912cecb232108",
        name="codex-reviewer",
        bundle=bundle,
    )

    resp = await agents_client.get("/v1/agents")

    assert resp.status_code == 200, resp.text
    entry = next(a for a in resp.json()["data"] if a["id"] == "77b35426aef3c1495c2912cecb232108")
    # availability gates whether the picker can launch the entry;
    # unavailable_reason explains a disabled one. Both absent from the
    # AgentObject schema today, so these key lookups fail (the xfail).
    assert "availability" in entry
    assert "unavailable_reason" in entry


async def test_catalog_description_falls_back_to_spec_when_row_unset(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    agents_client: httpx.AsyncClient,
) -> None:
    """
    ``GET /v1/agents`` surfaces the spec's top-level ``description`` when
    the stored agent row has none.

    Single-file YAML built-ins don't persist a description at
    registration today, so the stored column is ``None`` for them. The
    new-session picker shows a hover description, and without this
    fallback those agents would hover blank. Registering with
    ``description=None`` but a spec that declares one proves the route
    reads through to the bundle rather than echoing the empty column.
    """
    bundle = build_agent_bundle(
        name="hoverable-agent",
        description="Planned and split across sub-agents.",
    )
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id="7152784735902dd2219239e9577daf2d",
        name="hoverable-agent",
        bundle=bundle,
        description=None,
    )

    resp = await agents_client.get("/v1/agents")

    assert resp.status_code == 200, resp.text
    entry = next(a for a in resp.json()["data"] if a["id"] == "7152784735902dd2219239e9577daf2d")
    # The stored column is None, so a non-None value here can only have
    # come from the spec via the load → AgentObject fallback path.
    assert entry["description"] == "Planned and split across sub-agents."


async def test_catalog_description_prefers_stored_row_over_spec(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    agents_client: httpx.AsyncClient,
) -> None:
    """
    ``GET /v1/agents`` prefers the stored row's ``description`` over the
    spec's when both are set.

    The fallback to the spec must be exactly that — a fallback. An
    operator who set a description on the row (e.g. a curated catalog
    label) must not have it silently overridden by whatever the bundled
    spec happens to say. Registering with a stored description that
    differs from the spec's proves the stored value wins.
    """
    bundle = build_agent_bundle(
        name="relabelled-agent",
        description="Spec description (should be ignored).",
    )
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id="92b7d16d5c148ea2cfc43786bb1c696e",
        name="relabelled-agent",
        bundle=bundle,
        description="Curated catalog label.",
    )

    resp = await agents_client.get("/v1/agents")

    assert resp.status_code == 200, resp.text
    entry = next(a for a in resp.json()["data"] if a["id"] == "92b7d16d5c148ea2cfc43786bb1c696e")
    # Stored value present → it wins; the differing spec description
    # proves the route didn't blindly overwrite with the bundle's.
    assert entry["description"] == "Curated catalog label."


async def test_auto_select_disabled_profiles_remain_manually_selectable(
    agent_store: SqlAlchemyAgentStore,
    agents_client: httpx.AsyncClient,
) -> None:
    agent = agent_store.create("aa" * 16, "toggle-me", "aa/bundle", auto_select_enabled=True)
    agent_store.create("ab" * 16, "keep-enabled", "ab/bundle", auto_select_enabled=True)

    patched = await agents_client.patch(
        f"/v1/agents/{agent.id}", json={"auto_select_enabled": False}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["auto_select_enabled"] is False

    visible = await agents_client.get("/v1/agents")
    assert agent.id in {row["id"] for row in visible.json()["data"]}
    managed = await agents_client.get("/v1/agents?include_disabled=true")
    row = next(row for row in managed.json()["data"] if row["id"] == agent.id)
    assert row["enabled"] is True
    assert row["auto_select_enabled"] is False
    assert row["archived"] is False


async def test_builtin_delete_is_rejected(
    agent_store: SqlAlchemyAgentStore,
    agents_client: httpx.AsyncClient,
) -> None:
    agent_id = builtin_agent_id("protected")
    agent_store.create(agent_id, "protected", "protected/bundle")

    response = await agents_client.delete(f"/v1/agents/{agent_id}")

    assert response.status_code == 409
    assert agent_store.get(agent_id) is not None


async def test_custom_delete_archives_profile(
    agent_store: SqlAlchemyAgentStore,
    agents_client: httpx.AsyncClient,
) -> None:
    agent = agent_store.create("bb" * 16, "custom", "custom/bundle", auto_select_enabled=True)
    agent_store.create("bc" * 16, "remaining", "remaining/bundle", auto_select_enabled=True)

    response = await agents_client.delete(f"/v1/agents/{agent.id}")

    assert response.status_code == 204
    archived = agent_store.get(agent.id)
    assert archived is not None
    assert archived.archived is True
    assert archived.enabled is False


async def test_last_custom_profile_cannot_be_deleted(
    agent_store: SqlAlchemyAgentStore,
    agents_client: httpx.AsyncClient,
) -> None:
    agent = agent_store.create("bd" * 16, "only-profile", "only/bundle", auto_select_enabled=True)

    response = await agents_client.delete(f"/v1/agents/{agent.id}")

    assert response.status_code == 409
    assert "last profile" in response.json()["error"]["message"]
    assert agent_store.get(agent.id) is not None


async def test_multipart_create_persists_profile_and_metadata(
    agents_client: httpx.AsyncClient,
) -> None:
    bundle = build_agent_bundle(
        name="uploaded-profile",
        description="Created without a session",
        executor={"type": "omnigent", "model": "gpt-test", "config": {"harness": "codex"}},
        sub_agents=[{"name": "worker"}],
    )

    response = await agents_client.post(
        "/v1/agents",
        files={"bundle": ("profile.tar.gz", bundle, "application/gzip")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "uploaded-profile"
    assert body["description"] == "Created without a session"
    assert body["default_harness"] == "codex"
    assert body["default_model"] == "gpt-test"
    assert body["is_multi_agent"] is True
    assert body["subagent_count"] == 1
    duplicate = await agents_client.post(
        "/v1/agents",
        files={"bundle": ("profile.tar.gz", bundle, "application/gzip")},
    )
    assert duplicate.status_code == 409


async def test_custom_profile_can_edit_prompt_fields_in_place(
    agents_client: httpx.AsyncClient,
) -> None:
    bundle = build_agent_bundle(
        name="editable-profile",
        description="Before",
        skills=[
            {
                "name": "keep-me",
                "description": "Preserved capability",
                "content": "Keep this skill.",
            }
        ],
    )
    created = await agents_client.post(
        "/v1/agents",
        files={"bundle": ("profile.tar.gz", bundle, "application/gzip")},
    )
    agent_id = created.json()["id"]

    response = await agents_client.put(
        f"/v1/agents/{agent_id}",
        json={
            "name": "edited-profile",
            "description": "After",
            "instructions": "Updated instructions",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == agent_id
    assert body["name"] == "edited-profile"
    assert body["description"] == "After"
    assert body["instructions"] == "Updated instructions"
    assert [skill["name"] for skill in body["skills"]] == ["keep-me"]
    assert body["version"] == 2


class _FakeAutoSelectLLM:
    def __init__(self, selected_id: str) -> None:
        self.selected_id = selected_id
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[SimpleNamespace(text=self.selected_id)],
                )
            ]
        )


class _MalformedAutoSelectLLM:
    async def create(self, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(output=[])


async def test_auto_select_uses_only_enabled_profiles_and_returns_metadata(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    agents_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_id = "cc" * 16
    _register_builtin_agent(
        agent_store,
        artifact_store,
        agent_id=selected_id,
        name="orchestrator",
        bundle=build_agent_bundle(
            name="orchestrator",
            description="Delegates work",
            sub_agents=[{"name": "worker"}],
        ),
        auto_select_enabled=True,
    )
    agent_store.create(
        "dd" * 16,
        "disabled",
        "disabled/bundle",
        auto_select_enabled=False,
    )
    agent_store.create("ee" * 16, "role-only", "role/bundle", is_role=True)
    archived = agent_store.create(
        "ff" * 16, "archived", "archived/bundle", auto_select_enabled=True
    )
    agent_store.archive(archived.id)
    agent_store.create("11" * 16, "claude-native-ui", "native/bundle")
    agent_store.create("22" * 16, "kimi", "hidden/bundle")
    fake = _FakeAutoSelectLLM(selected_id)
    monkeypatch.setattr(
        "omnigent.profile_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.profile_selection.build_server_llm_client",
        lambda _config: fake,
    )

    response = await agents_client.post(
        "/v1/agents/auto-select",
        json={"input": "Plan and implement a broad refactor"},
    )

    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile["id"] == selected_id
    assert profile["is_multi_agent"] is True
    assert profile["subagent_count"] == 1
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert "untrusted data" in call["instructions"]
    context = json.loads(call["input"][0]["content"][0]["text"])
    assert [candidate["profile_id"] for candidate in context["candidates"]] == [selected_id]
    assert "tools" not in call


async def test_auto_select_rejects_unknown_llm_selection(
    agent_store: SqlAlchemyAgentStore,
    agents_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_store.create("12" * 16, "candidate", "candidate/bundle", auto_select_enabled=True)
    fake = _FakeAutoSelectLLM("not-a-candidate")
    monkeypatch.setattr(
        "omnigent.profile_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.profile_selection.build_server_llm_client",
        lambda _config: fake,
    )

    response = await agents_client.post(
        "/v1/agents/auto-select",
        json={"input": "Write a short summary"},
    )

    assert response.status_code == 409
    assert "invalid or unknown profile ID" in response.json()["error"]["message"]


async def test_auto_select_rejects_malformed_llm_output(
    agent_store: SqlAlchemyAgentStore,
    agents_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_store.create("23" * 16, "candidate", "candidate/bundle", auto_select_enabled=True)
    monkeypatch.setattr(
        "omnigent.profile_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.profile_selection.build_server_llm_client",
        lambda _config: _MalformedAutoSelectLLM(),
    )

    response = await agents_client.post(
        "/v1/agents/auto-select",
        json={"input": "Write a short summary"},
    )

    assert response.status_code == 409
    assert "malformed profile selection" in response.json()["error"]["message"]


async def test_auto_select_unavailable_without_server_ai(
    agent_store: SqlAlchemyAgentStore,
    agents_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_store.create("34" * 16, "candidate", "candidate/bundle", auto_select_enabled=True)
    monkeypatch.setattr(
        "omnigent.profile_selection.get_caps",
        lambda: SimpleNamespace(llm=None),
    )

    response = await agents_client.post(
        "/v1/agents/auto-select",
        json={"input": "Help me debug this"},
    )

    assert response.status_code == 409
    assert "no server AI backend" in response.json()["error"]["message"]


async def test_auto_select_rejects_empty_candidate_set(
    agents_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "omnigent.profile_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )

    response = await agents_client.post(
        "/v1/agents/auto-select",
        json={"input": "Help me debug this"},
    )

    assert response.status_code == 409
    assert "no enabled profiles" in response.json()["error"]["message"]


async def test_auto_select_validates_non_empty_bounded_input(
    agents_client: httpx.AsyncClient,
) -> None:
    blank = await agents_client.post("/v1/agents/auto-select", json={"input": "   "})
    oversized = await agents_client.post(
        "/v1/agents/auto-select",
        json={"input": "x" * 20_001},
    )

    assert blank.status_code == 422
    assert oversized.status_code == 422
