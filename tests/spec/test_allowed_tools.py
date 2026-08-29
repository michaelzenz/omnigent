"""Tests for the agent-level ``allowed_tools`` allowlist."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from omnigent.spec import load
from omnigent.spec.types import AgentSpec
from omnigent.tools.preferences import filter_tool_schemas_by_allowlist


def _make_schemas(names: list[str]) -> list[dict]:
    """Build minimal OpenAI-format schemas for the given tool names."""
    return [{"type": "function", "function": {"name": n}} for n in names]


class TestFilterByAllowlist:
    def test_none_returns_all(self):
        schemas = _make_schemas(["load_skill", "sys_os_read"])
        assert filter_tool_schemas_by_allowlist(schemas, None) == schemas

    def test_empty_list_returns_all(self):
        """Empty list is falsy — treated as 'no allowlist' (matches parser behavior)."""
        schemas = _make_schemas(["load_skill", "sys_os_read"])
        assert filter_tool_schemas_by_allowlist(schemas, []) == schemas

    def test_filters_to_allowed(self):
        schemas = _make_schemas(["load_skill", "sys_os_read", "sys_os_write"])
        result = filter_tool_schemas_by_allowlist(schemas, ["load_skill", "sys_os_read"])
        names = [s["function"]["name"] for s in result]
        assert names == ["load_skill", "sys_os_read"]

    def test_mcp_namespaced_names(self):
        schemas = _make_schemas(["load_skill", "github__list_issues", "github__create_pr"])
        result = filter_tool_schemas_by_allowlist(schemas, ["github__list_issues"])
        names = [s["function"]["name"] for s in result]
        assert names == ["github__list_issues"]

    def test_unknown_name_in_allowlist_is_noop(self):
        schemas = _make_schemas(["load_skill"])
        result = filter_tool_schemas_by_allowlist(schemas, ["load_skill", "nonexistent_tool"])
        assert len(result) == 1
        assert result[0]["function"]["name"] == "load_skill"

    def test_preserves_order(self):
        schemas = _make_schemas(["c", "a", "b"])
        result = filter_tool_schemas_by_allowlist(schemas, ["a", "b", "c"])
        names = [s["function"]["name"] for s in result]
        assert names == ["c", "a", "b"]


class TestAgentSpecField:
    def test_default_is_none(self):
        spec = AgentSpec(spec_version=1)
        assert spec.allowed_tools is None

    def test_set_explicitly(self):
        spec = AgentSpec(spec_version=1, allowed_tools=["load_skill"])
        assert spec.allowed_tools == ["load_skill"]


class TestYamlParsing:
    def test_parse_allowed_tools(self, tmp_path: Path):
        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text(
            textwrap.dedent(
                """
                name: restricted
                prompt: |
                  You are a test agent.
                executor:
                  harness: claude-sdk
                  model: test-model
                allowed_tools:
                  - load_skill
                  - read_skill_file
                  - sys_os_read
                """
            )
        )
        spec = load(yaml_file)
        assert spec.allowed_tools == ["load_skill", "read_skill_file", "sys_os_read"]

    def test_parse_no_allowed_tools(self, tmp_path: Path):
        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text(
            textwrap.dedent(
                """
                name: unrestricted
                prompt: |
                  You are a test agent.
                executor:
                  harness: claude-sdk
                  model: test-model
                """
            )
        )
        spec = load(yaml_file)
        assert spec.allowed_tools is None

    def test_parse_empty_allowed_tools(self, tmp_path: Path):
        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text(
            textwrap.dedent(
                """
                name: empty_allow
                prompt: |
                  You are a test agent.
                executor:
                  harness: claude-sdk
                  model: test-model
                allowed_tools: []
                """
            )
        )
        spec = load(yaml_file)
        assert spec.allowed_tools is None

    def test_parse_non_list_raises(self, tmp_path: Path):
        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text(
            textwrap.dedent(
                """
                name: bad_type
                prompt: |
                  You are a test agent.
                executor:
                  harness: claude-sdk
                  model: test-model
                allowed_tools: "not-a-list"
                """
            )
        )
        from omnigent.errors import OmnigentError

        with pytest.raises(OmnigentError, match="allowed_tools"):
            load(yaml_file)
