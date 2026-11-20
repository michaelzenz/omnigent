"""Scheduled-task mutations are not gated by a mandatory approval policy.

``build_policy_engine`` still injects ``__ask_on_add_policy``. Create and
update of scheduled tasks follow the same no-policy fast path as list and
delete when no other guardrails apply.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.entities import Conversation
from omnigent.policies.types import EvaluationContext
from omnigent.runtime.policies.builder import any_policies_apply, build_policy_engine
from omnigent.spec.parser import parse
from omnigent.spec.types import AgentSpec, Phase, PolicyAction
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)

_TASK_ID = "0123456789abcdef0123456789abcdef"
_CREATE_ARGS = {
    "name": "nightly triage",
    "prompt": "Triage new issues.",
    "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
    "agent_id": "ag_1",
    "permission_mode": None,
}


def _no_guardrails_spec(
    tmp_path: Path,
    conversation_store: SqlAlchemyConversationStore,
) -> tuple[AgentSpec, Conversation]:
    (tmp_path / "config.yaml").write_text("spec_version: 1\nname: scheduler-probe\n")
    return parse(tmp_path), conversation_store.create_conversation()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        ("sys_add_policy", {"name": "shell_gate", "handler": "x.y"}, PolicyAction.ASK),
        ("sys_scheduled_task_create", _CREATE_ARGS, PolicyAction.ALLOW),
        (
            "sys_scheduled_task_update",
            {
                "scheduled_task_id": _TASK_ID,
                "state": "paused",
                "prompt": "Triage new issues.",
                "permission_mode": "bypassPermissions",
            },
            PolicyAction.ALLOW,
        ),
    ],
    ids=["add_policy_control", "create", "update"],
)
async def test_scheduled_task_mutation_allows_without_guardrails(
    tmp_path: Path,
    conversation_store: SqlAlchemyConversationStore,
    tool_name: str,
    arguments: dict[str, object],
    expected: PolicyAction,
) -> None:
    spec, conv = _no_guardrails_spec(tmp_path, conversation_store)
    engine = build_policy_engine(
        spec=spec,
        conversation_id=conv.id,
        conversation_store=conversation_store,
    )
    result = await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL,
            content={"name": tool_name, "arguments": arguments},
            tool_name=tool_name,
        )
    )
    assert result.action == expected, result.reason


def test_no_policy_fast_path_keeps_add_policy_gate(
    tmp_path: Path,
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    spec, conv = _no_guardrails_spec(tmp_path, conversation_store)
    assert any_policies_apply(
        spec=spec,
        conversation_id=conv.id,
        default_policies=None,
        policy_store=None,
        phase=Phase.TOOL_CALL,
        tool_name="sys_add_policy",
        conversation=conv,
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "sys_scheduled_task_create",
        "sys_scheduled_task_update",
        "sys_scheduled_task_list",
        "sys_scheduled_task_delete",
    ],
)
def test_no_policy_fast_path_skips_scheduled_task_tools(
    tmp_path: Path,
    conversation_store: SqlAlchemyConversationStore,
    tool_name: str,
) -> None:
    spec, conv = _no_guardrails_spec(tmp_path, conversation_store)
    assert not any_policies_apply(
        spec=spec,
        conversation_id=conv.id,
        default_policies=None,
        policy_store=None,
        phase=Phase.TOOL_CALL,
        tool_name=tool_name,
        conversation=conv,
    )
