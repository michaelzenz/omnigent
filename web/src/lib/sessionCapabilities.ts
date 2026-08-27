/** UI-only session capability gates, derived from snapshot labels. */

import { isOnihPiTargetName } from "@/lib/omniharnessModels";

const CLAUDE_NATIVE_WRAPPER = "claude-code-native-ui";
const CODEX_NATIVE_WRAPPER = "codex-native-ui";
const PI_NATIVE_WRAPPER = "pi-native-ui";

/**
 * Fail-closed gate for Web UI reasoning-effort controls.
 *
 * :param session: Session or sidebar row carrying labels. ``null`` or missing
 *     labels fail closed.
 * :returns: True only for native sessions with Web UI effort controls, or
 *     onih-pi sessions (the Pi engine applies the effort on its next turn
 *     from the forwarded turn body). Other onih targets and pi-sdk agents
 *     have no ladder wired yet. cursor-native is intentionally excluded: its
 *     effort lives on the /model picker's per-model "Tab to modify" axis
 *     and a model switch resets it to that model's default, so a Web UI
 *     effort dial would silently diverge from the TUI. cursor-native
 *     supports model switching only for now.
 */
export function supportsEffortControl(
  session: {
    labels?: Record<string, string | null> | null;
    agentName?: string | null;
  } | null | undefined,
): boolean {
  const wrapper = session?.labels?.["omnigent.wrapper"];
  return (
    wrapper === CLAUDE_NATIVE_WRAPPER ||
    wrapper === CODEX_NATIVE_WRAPPER ||
    wrapper === PI_NATIVE_WRAPPER ||
    isOnihPiTargetName(session?.agentName)
  );
}
