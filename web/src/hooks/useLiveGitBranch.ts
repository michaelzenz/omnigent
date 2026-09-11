// Live git branch for the composer status line.
//
// The conversation row's `gitBranch` is written once at session creation,
// so the status-line branch goes stale the moment anyone switches branches
// inside the worktree. This hook polls the lightweight
// `GET /sessions/{id}/git-branch` endpoint (one `host.stat` frame per
// poll — the host already detects the branch during its stat round-trip)
// and writes corrections into chatStore, where `ComposerStatusLine` and
// the header menu read the branch from.
//
// Polling (rather than an SSE push) is deliberate: a branch switch inside
// the worktree happens entirely on the host machine with no Omnigent
// process involved, so there is no in-band event to subscribe to. The
// poll is cheap and pauses when the tab is hidden.

import { useEffect } from "react";

import { getSessionGitBranch } from "@/lib/sessionsApi";
import { setterFor } from "@/store/chatStore";

/** Poll cadence while the chat page is visible. */
const POLL_INTERVAL_MS = 10_000;

/**
 * Keep a session's `gitBranch` in chatStore current.
 *
 * Pass the bound conversation id (from `useChatStore`) — `null` disables.
 * Fetches immediately, then on an interval; every fetch asks the server to
 * re-read the branch from the host, so a switch lands within one interval
 * of returning to the tab. Failures are silently ignored (offline host,
 * server restart mid-poll) — the last known branch stays on screen.
 */
export function useLiveGitBranch(conversationId: string | null | undefined): void {
  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async (): Promise<void> => {
      if (cancelled) return;
      // Skip while hidden — a background tab polling every 10s is waste.
      if (document.visibilityState === "visible") {
        try {
          const branch = await getSessionGitBranch(conversationId);
          if (!cancelled && branch != null) {
            // Named-conversation setter: a poll that lands after the user
            // switched chats writes the fetched conversation, never the
            // visible one (and no-ops once the entry is evicted).
            setterFor(conversationId)({ gitBranch: branch });
          }
        } catch {
          // Offline host / server restarting — keep the last known branch.
        }
      }
      if (!cancelled) timer = setTimeout(tick, POLL_INTERVAL_MS);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [conversationId]);
}
