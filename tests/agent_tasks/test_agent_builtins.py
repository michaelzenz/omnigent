"""Tests for packaged PuppyGarden role agents."""

from pathlib import Path

import yaml

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_AGENT_NAME,
    bundle_task_instruction_includes,
    task_agent_spec_path,
)
from omnigent.inner.loader import load_agent_def


def test_packaged_role_bundle_includes_manuals(tmp_path: Path) -> None:
    """Role bundles carry their manuals and append them to instructions."""
    source = task_agent_spec_path(TASK_BROKER_AGENT_NAME)
    document = yaml.safe_load(source.read_text())
    assert isinstance(document, dict)

    bundle_task_instruction_includes(document, tmp_path)
    bundled_spec = tmp_path / source.name
    bundled_spec.write_text(yaml.safe_dump(document, sort_keys=False))

    assert document["instructions_include"] == [
        "instructions/README.md",
        "instructions/TASK_BROKER.md",
    ]
    assert (tmp_path / "instructions" / "README.md").is_file()
    assert (tmp_path / "instructions" / "TASK_BROKER.md").is_file()

    agent = load_agent_def(bundled_spec)
    assert agent.instructions is not None
    assert "PuppyGarden Task System" in agent.instructions
    assert "Task broker manual" in agent.instructions
