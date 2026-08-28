// Per-row state derivation for the sidebar badge.
// Priority: awaiting > running > no badge.
//
// Liveness (runner / host reachability) is no longer a sidebar state:
// it surfaces in the open-session view (see `useSessionLiveness`), so the
// sidebar no longer renders a "disconnected" badge and `getSessionState`
// no longer reads runner liveness.
//
// "failed" is intentionally not a sidebar state either — the chat surface
// is the right place to read what failed. Conflating it into the same red
// badge also led to a stale-cache bug where a prior turn's
// `_session_status_cache["failed"]` would mask a fresh elicitation.

import type { Conversation } from "@/hooks/useConversations";
import type { WorktreeStatus } from "@/lib/types";

export type SessionState =
  | { kind: "awaiting"; count: number }
  | { kind: "running" }
  | { kind: "unseen" }
  // The open session's launch/relaunch window — a send in flight or the PTY
  // being created before the server confirms `running`. Not derivable from a
  // conversation row (it reads the chat store), so `getSessionState` never
  // returns it; the sidebar row folds it in for the bound session only.
  | { kind: "starting" };

export function getSessionState(
  conversation: Pick<Conversation, "status" | "pending_elicitations_count" | "worktree_status"> | undefined | null,
): SessionState | null {
  const pending = conversation?.pending_elicitations_count ?? 0;
  if (pending > 0) return { kind: "awaiting", count: pending };
  if (conversation?.status === "running") return { kind: "running" };
  // A background session whose worktree is still being created (or whose
  // runner is launching after worktree creation) shows the same spinner as
  // a starting session — the server-side status is still "idle" during
  // this phase, so without this the sidebar shows nothing.
  if (conversation?.worktree_status && conversation.worktree_status.stage !== "failed") {
    return { kind: "starting" };
  }
  return null;
}
