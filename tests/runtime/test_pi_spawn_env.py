"""
Tests for ``_build_pi_spawn_env`` in ``omnigent/runtime/workflow.py``.

The spawn-env builder maps ``spec.executor`` fields to ``HARNESS_PI_*``
env vars that the pi harness wrap reads at executor-construction time.
Mirrors ``test_claude_sdk_spawn_env.py`` — pi must have the same
Databricks-gateway default-model parity that claude-sdk has.

This is a unit test — no subprocess spawn, no real pi CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.inference_proxy import (
    HARNESS_PI_SERVER_PROXY_ENV,
    HOST_INFERENCE_PROXY_TOKEN_ENV,
    HOST_INFERENCE_PROXY_URL_ENV,
    PI_INFERENCE_PROXY_TOKEN_ENV,
)
from omnigent.pi_local_config import (
    HARNESS_PI_LOCAL_CONFIG_DIR_ENV,
    HARNESS_PI_LOCAL_PROVIDER_IDS_ENV,
    PiLocalConfig,
)
from omnigent.runtime.workflow import _build_pi_spawn_env
from omnigent.spec import load
from omnigent.spec.types import AgentSpec, ExecutorSpec, LLMConfig


@pytest.fixture(autouse=True)
def _isolate_global_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    Point OMNIGENT_CONFIG_HOME at an empty temp dir for every test in
    this file so the developer's real ``~/.omnigent/config.yaml`` (e.g.
    a default provider) cannot hijack the legacy-profile path under test.

    ``HOME`` is redirected there too. An empty omnigent config is not enough
    isolation on its own: ambient provider detection also reads
    ``~/.codex/config.toml`` and ``~/.databrickscfg``, so a developer whose
    codex config pins a Databricks gateway has pi consume that detected
    cli-config provider and stall in databricks-sdk workspace lookups.
    ``USERPROFILE`` is the Windows spelling of the same home.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary directory for the isolated config and home.
    """
    monkeypatch.setenv("OMNIGENT_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv(HOST_INFERENCE_PROXY_URL_ENV, raising=False)
    monkeypatch.delenv(HOST_INFERENCE_PROXY_TOKEN_ENV, raising=False)
    monkeypatch.setattr(
        "omnigent.runtime.workflow._resolve_catalog_default_model",
        lambda provider_name, family, *, context: f"catalog-{provider_name}-{family}-default",
    )
    monkeypatch.setattr(
        "omnigent.runtime.workflow.resolve_usable_pi_local_config",
        lambda: None,
    )


def _make_spec(*, model: str | None = None, profile: str | None = None) -> AgentSpec:
    """
    Build a minimal pi :class:`AgentSpec` for spawn-env tests.

    :param model: Model identifier threaded into executor config and
        ``spec.llm``, e.g. ``"databricks-claude-sonnet-4-6"``. ``None``
        omits it (no model pinned in YAML — the nessie shape).
    :param profile: Legacy profile set via ``executor.config["profile"]``.
        ``None`` omits it (no profile declared in YAML).
    :returns: A populated :class:`AgentSpec`.
    """
    config: dict[str, object] = {"harness": "pi"}
    if model is not None:
        config["model"] = model
    if profile is not None:
        config["profile"] = profile
    return AgentSpec(
        spec_version=1,
        name="test-pi",
        instructions="You are a test agent.",
        executor=ExecutorSpec(type="omnigent", config=config, model=model),
        llm=LLMConfig(model=model) if model is not None else None,
    )


def test_builtin_onih_pi_pins_a_default_model() -> None:
    """Headless automation spawns onih-pi with no session model override;
    server-proxied Pi raises without a spec default. The pin lives at the
    executor block's top level — ``config.model`` is never read on the pi
    spawn path."""
    spec = load(Path("omnigent/resources/examples/onih-pi"))

    assert spec.executor.model == "databricks-glm-5-3-flash"
    env = _build_pi_spawn_env(spec, workdir=None)
    assert env["HARNESS_PI_MODEL"] == "databricks-glm-5-3-flash"


def test_builtin_onih_pmv2_maps_pi_executor_config(tmp_path: Path) -> None:
    """
    The onih-pmv2 bundle (broker/manager restricted profile) runs
    the pi harness with a rolling context window. Its stringified
    executor booleans map to the ``HARNESS_PI_*`` env vars the harness
    wrap reads.

    Regression guard: the spec parser stringifies scalar executor config
    values, so ``native_tools: false`` arrives as ``"False"`` — the
    stringified-boolean branch in ``_build_pi_spawn_env`` must handle it.
    """
    spec = load(Path("omnigent/resources/examples/onih-pmv2"))

    assert spec.executor.harness_kind == "pi"
    assert "pmv2_api" in (spec.allowed_builtin_tools or [])
    assert "*__*" not in (spec.allowed_builtin_tools or [])
    assert spec.skills_filter == "none"
    assert spec.history_window_turns == 5

    env = _build_pi_spawn_env(spec, workdir=None)

    assert env["HARNESS_PI_PERSISTENT_SESSION"] == "1"
    assert env["HARNESS_PI_CANONICAL_REBUILD"] == "1"
    assert env["HARNESS_PI_ISOLATED_RESOURCES"] == "1"
    assert env["HARNESS_PI_NATIVE_TOOLS"] == "0"
    assert env["HARNESS_PI_NATIVE_SKILLS"] == "0"
    assert env["HARNESS_PI_SYSTEM_PROMPT_MODE"] == "replace"
    assert env["HARNESS_PI_SKILLS_FILTER"] == '"none"'
    assert env["HARNESS_PI_HISTORY_WINDOW_TURNS"] == "5"


def test_server_proxy_configures_pi_without_remote_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(HOST_INFERENCE_PROXY_URL_ENV, "http://127.0.0.1:43127/v1/inference")
    monkeypatch.setenv(HOST_INFERENCE_PROXY_TOKEN_ENV, "proxy-secret")

    env = _build_pi_spawn_env(_make_spec(), workdir=None)

    assert env[HARNESS_PI_SERVER_PROXY_ENV] == "true"
    assert env["HARNESS_PI_GATEWAY"] == "true"
    assert env["HARNESS_PI_GATEWAY_HOST"] == "http://127.0.0.1:43127/v1/inference"
    assert env[PI_INFERENCE_PROXY_TOKEN_ENV] == "proxy-secret"
    assert PI_INFERENCE_PROXY_TOKEN_ENV in env["HARNESS_PI_GATEWAY_AUTH_COMMAND"]
    assert "HARNESS_PI_DATABRICKS_PROFILE" not in env


def test_ucode_configured_pi_is_preferred_over_server_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / ".pi" / "agent"
    monkeypatch.setattr(
        "omnigent.runtime.workflow.resolve_usable_pi_local_config",
        lambda: PiLocalConfig(agent_dir, ("databricks-claude",)),
    )
    monkeypatch.setenv(HOST_INFERENCE_PROXY_URL_ENV, "http://127.0.0.1:43127/v1/inference")
    monkeypatch.setenv(HOST_INFERENCE_PROXY_TOKEN_ENV, "proxy-secret")

    env = _build_pi_spawn_env(_make_spec(), workdir=None)

    assert env[HARNESS_PI_LOCAL_CONFIG_DIR_ENV] == str(agent_dir)
    assert env[HARNESS_PI_LOCAL_PROVIDER_IDS_ENV] == '["databricks-claude"]'
    # The proxy remains available for a model absent from local ucode config;
    # PiExecutor chooses the local provider first for matching model IDs.
    assert env[HARNESS_PI_SERVER_PROXY_ENV] == "true"


def test_ucode_configured_pi_runs_without_server_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / ".pi" / "agent"
    monkeypatch.delenv(HOST_INFERENCE_PROXY_URL_ENV, raising=False)
    monkeypatch.delenv(HOST_INFERENCE_PROXY_TOKEN_ENV, raising=False)
    monkeypatch.setattr(
        "omnigent.runtime.workflow.resolve_usable_pi_local_config",
        lambda: PiLocalConfig(agent_dir, ("databricks-openai",)),
    )

    env = _build_pi_spawn_env(_make_spec(), workdir=None)

    assert env[HARNESS_PI_LOCAL_CONFIG_DIR_ENV] == str(agent_dir)
    assert HARNESS_PI_SERVER_PROXY_ENV not in env
    assert "HARNESS_PI_GATEWAY" not in env


def test_pi_spawn_env_threads_cwd_separately_from_bundle_dir(tmp_path: Path) -> None:
    """
    Pi gets the session workspace as ``HARNESS_PI_CWD``.

    ``workdir`` is the extracted agent bundle, not the user's project
    workspace. If these are conflated, Pi launches in the wrong repository.
    """
    workspace = tmp_path / "repo"
    workspace.mkdir()
    bundle_dir = tmp_path / "runner-specs" / "ag_pi-v1"
    bundle_dir.mkdir(parents=True)

    env = _build_pi_spawn_env(_make_spec(), cwd=workspace, workdir=bundle_dir)

    assert env["HARNESS_PI_CWD"] == str(workspace)
    assert env["HARNESS_PI_BUNDLE_DIR"] == str(bundle_dir)


def _ucode_state_for_pi(
    monkeypatch: pytest.MonkeyPatch, *, model: str | None, with_pi_entry: bool
):
    """
    Mock ucode resolution to a workspace state with or without a pi agent.

    Builds a workspace state whose ``pi`` agent carries gateway URLs +
    auth command but ``model=model``, then monkeypatches the workflow
    module's ucode lookups to return it.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param model: Per-agent ucode model, e.g. ``None`` to simulate a
        workspace that caches no model, or ``"databricks-claude-sonnet-4-6"``.
    :param with_pi_entry: ``False`` builds a state with no ``pi`` agent
        entry at all, exercising the early-return in
        ``configure_agent_harness_with_ucode``.
    """
    from omnigent.onboarding.ucode_state import UcodeAgentState, UcodeWorkspaceState

    agents = (
        {
            "pi": UcodeAgentState(
                model=model,
                base_urls={
                    "claude": "https://example.databricks.com/ai-gateway/anthropic",
                    "openai": "https://example.databricks.com/ai-gateway/codex/v1",
                },
                auth_command="printf token",
            )
        }
        if with_pi_entry
        else {}
    )
    state = UcodeWorkspaceState(
        workspace_url="https://example.databricks.com",
        agents=agents,
    )
    monkeypatch.setattr(
        "omnigent.runtime.workflow.get_workspace_url_for_profile",
        lambda profile: "https://example.databricks.com",
    )
    monkeypatch.setattr(
        "omnigent.runtime.workflow.read_ucode_state",
        lambda workspace_url: state,
    )


def test_ucode_state_without_model_falls_back_to_databricks_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A modelless ucode state resolves the Databricks gateway default model.

    Reproduces the nessie failure shape on pi: a profile-backed pi agent
    with no spec model, whose workspace ucode state caches gateway URLs but
    no model. Without the producer default pi falls back to its own host
    default (an Anthropic-direct id the gateway rejects), so the model env
    var must be set to a routable ``databricks-*`` endpoint name.
    """
    _ucode_state_for_pi(monkeypatch, model=None, with_pi_entry=True)

    spec = _make_spec(model=None, profile="oss")
    env = _build_pi_spawn_env(spec, workdir=None)

    assert env["HARNESS_PI_GATEWAY"] == "true"
    # The verified routable gateway endpoint name, not pi's own default.
    assert env["HARNESS_PI_MODEL"] == "catalog-databricks-claude-default"


def test_ucode_state_with_model_is_not_overridden_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A ucode-supplied model is used as-is; the default does not clobber it.

    Failure means the producer's missing-model fallback would override a
    workspace that correctly caches its own model.
    """
    _ucode_state_for_pi(monkeypatch, model="databricks-claude-sonnet-4-6", with_pi_entry=True)

    spec = _make_spec(model=None, profile="oss")
    env = _build_pi_spawn_env(spec, workdir=None)

    assert env["HARNESS_PI_MODEL"] == "databricks-claude-sonnet-4-6"


def test_spec_model_wins_over_ucode_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A spec-pinned model takes precedence over both ucode and the default.

    Failure means the ucode/default plumbing clobbers an explicit
    ``executor.model`` from the agent YAML.
    """
    _ucode_state_for_pi(monkeypatch, model=None, with_pi_entry=True)

    spec = _make_spec(model="databricks-gpt-5-4", profile="oss")
    env = _build_pi_spawn_env(spec, workdir=None)

    assert env["HARNESS_PI_MODEL"] == "databricks-gpt-5-4"


def test_no_ucode_pi_entry_leaves_model_to_executor_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Without a ucode ``pi`` entry the producer sets no model env var.

    ``configure_agent_harness_with_ucode`` early-returns before its
    default-model fallback when the workspace state has no ``pi`` agent.
    The spawn env must still enable the gateway + carry the profile so
    the executor's own profile-derived Databricks default (see
    ``PiExecutor._resolve_model``) covers this path — asserting the model
    var is absent proves that executor-side fallback is actually reached.
    """
    _ucode_state_for_pi(monkeypatch, model=None, with_pi_entry=False)

    spec = _make_spec(model=None, profile="oss")
    env = _build_pi_spawn_env(spec, workdir=None)

    assert env["HARNESS_PI_GATEWAY"] == "true"
    assert env["HARNESS_PI_DATABRICKS_PROFILE"] == "oss"
    # No producer model — the executor's profile-path default applies.
    assert "HARNESS_PI_MODEL" not in env


def test_history_window_absent_by_default_and_off_by_default() -> None:
    """No ``history_window_turns`` in the spec → no window env var.

    Ordinary Pi agents keep full history; only bundles that opt in
    (onih-pmv2) carry the env.
    """
    spec = load(Path("omnigent/resources/examples/onih-pi"))
    assert spec.history_window_turns is None

    env = _build_pi_spawn_env(spec, workdir=None)
    assert "HARNESS_PI_HISTORY_WINDOW_TURNS" not in env


def test_history_window_env_maps_to_launch_option(monkeypatch: pytest.MonkeyPatch) -> None:
    """``HARNESS_PI_HISTORY_WINDOW_TURNS`` reaches PiLaunchOptions."""
    from omnigent.inner.pi_harness import _build_pi_executor

    monkeypatch.setenv("HARNESS_PI_HISTORY_WINDOW_TURNS", "3")
    executor = _build_pi_executor()
    assert executor._launch_options.history_window_turns == 3

    monkeypatch.delenv("HARNESS_PI_HISTORY_WINDOW_TURNS")
    executor = _build_pi_executor()
    assert executor._launch_options.history_window_turns == 0
