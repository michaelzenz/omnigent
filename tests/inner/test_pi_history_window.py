"""Tests for the stateless-router history window in the Pi executor.

``history_window_turns`` bounds the serialized "Conversation so far"
prompt to the last N user turns — PuppyGarden broker/manager keep state
in the task store, not in chat history.
"""

from __future__ import annotations

import shutil

from omnigent.inner.pi_executor import _build_pi_prompt, _window_history_tail

Msg = dict


def _history(user_turns: int) -> list[Msg]:
    """Build a user/assistant alternating history with *user_turns* turns."""
    msgs: list[Msg] = []
    for i in range(user_turns):
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"u{i}"}]})
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]})
    return msgs


class TestWindowHistoryTail:
    def test_no_window_returns_all(self):
        h = _history(4)
        assert _window_history_tail(h, 0) is h

    def test_window_larger_than_history_returns_all(self):
        h = _history(2)
        assert _window_history_tail(h, 5) == h

    def test_window_keeps_last_n_user_turns(self):
        h = _history(4)
        kept = _window_history_tail(h, 2)
        texts = [m["content"][0]["text"] for m in kept]
        # Last 2 user turns + their interleaved assistant replies.
        assert texts == ["u2", "a2", "u3", "a3"]

    def test_window_keeps_trailing_non_user_items(self):
        h = _history(2)
        h.append({"role": "tool", "content": "x"})  # trailing item after last turn
        kept = _window_history_tail(h, 1)
        assert [m["content"][0]["text"] for m in kept[:2]] == ["u1", "a1"]
        assert kept[-1] == h[-1]

    def test_window_keeps_tool_results_within_kept_turns(self):
        """A turn = user + assistant (with tool calls) + tool results.

        Pi context roles are user / assistant / toolResult (pi-ai types).
        The kept tail must carry complete exchanges — tool results inside
        the window ride along with their assistant turn.
        """
        h: list[Msg] = []
        for i in range(3):
            h.append({"role": "user", "content": [{"type": "text", "text": f"u{i}"}]})
            h.append({"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]})
            h.append({"role": "toolResult", "toolCallId": f"c{i}", "toolName": "t",
                      "content": [{"type": "text", "text": f"r{i}"}]})
        kept = _window_history_tail(h, 1)
        # Only the last exchange survives: u2 + a2 + its tool result.
        assert [m["role"] for m in kept] == ["user", "assistant", "toolResult"]
        assert kept[0]["content"][0]["text"] == "u2"
        assert kept[2]["content"][0]["text"] == "r2"


class TestBuildPiPromptWindow:
    def test_windowed_prompt_carries_only_tail(self):
        prompt = _build_pi_prompt(_history(5), is_first_turn=True, history_window_turns=2)
        assert isinstance(prompt, str)
        assert "u3" in prompt and "u4" in prompt
        assert "u0" not in prompt and "u1" not in prompt and "u2" not in prompt
        assert prompt.startswith("Conversation so far:")

    def test_window_off_serializes_full_history(self):
        prompt = _build_pi_prompt(_history(5), is_first_turn=True, history_window_turns=0)
        assert isinstance(prompt, str)
        assert "u0" in prompt and "u4" in prompt

    def test_single_turn_returns_latest_content_regardless(self):
        msgs = _history(1)
        prompt = _build_pi_prompt(msgs, is_first_turn=True, history_window_turns=2)
        # 1 user message → not the "Conversation so far" branch.
        assert prompt == "u0"


class TestExtensionSourceWindow:
    """The generated Pi extension carries the ``context`` + ``before_agent_start`` hooks.

    Pi fires ``context`` before every LLM call and the returned messages
    replace the model input; ``before_agent_start`` can override the system
    prompt for the turn (reset to base afterwards). Together they give the
    notice wire-level delivery without respawning the process.
    """

    def _source(self, window: int) -> str:
        from omnigent.inner.pi_executor import _generate_extension_js

        return _generate_extension_js(
            port=54321,
            tool_schemas=[{"name": "t", "description": "", "parameters": {}}],
            token="tok",
            history_window_turns=window,
        )

    def test_hooks_present_with_window(self):
        src = self._source(2)
        assert 'pi.on("context"' in src
        assert 'pi.on("before_agent_start"' in src
        assert "const HISTORY_WINDOW = 2" in src
        assert 'event.messages[i].role === "user"' in src

    def test_hook_baked_off_without_window(self):
        src = self._source(0)
        assert "const HISTORY_WINDOW = 0" in src
        # The before_agent_start injection is independent of the window —
        # it gates on the notice marker, not on HISTORY_WINDOW.
        assert 'pi.on("before_agent_start"' in src

    def test_no_split_hooks(self):
        """Notice split moved server-side (events=user msg, roster=
        instructions) — the extension carries only the history window."""
        src = self._source(2)
        assert "before_agent_start" not in src
        assert "EVENTS_MARKER" not in src
        assert "Task roster" not in src

    def test_generated_source_is_valid_js_syntax(self):
        import subprocess

        src = self._source(2)
        node = shutil.which("node")
        if node is None:
            return  # JS runtime unavailable in CI — skip syntax check
        result = subprocess.run(
            [node, "--check", "/dev/stdin"],
            input=src, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
