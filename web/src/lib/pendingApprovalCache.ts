// Client-side optimistic-override cache for pending-elicitation counts.
//
// When the user approves a tool call, the server decrements its in-memory
// count synchronously but persists the new count to the DB asynchronously
// (a background worker thread). A list refetch that races the DB write
// gets a stale count via the server's cross-replica `max()` fallback.
//
// To cover that window, the client records the server timestamp returned
// by the resolve POST (`server_time`). When computing the inbox badge,
// any session whose `pending_elicitations_updated_at` is ≤ the recorded
// approval time is treated as count 0 — the server count predates the
// verdict. The override clears automatically once the server pushes a
// newer timestamp (a new elicitation or a WS rescan after the DB write
// lands).

/** Map of session id → server timestamp from the resolve response. */
const approvalTimes = new Map<string, number>();

/**
 * Record that the user approved an elicitation on `sessionId` at
 * `serverTime` (epoch seconds, from the resolve response's
 * `server_time`). Subsequent badge-count reads treat this session's
 * `pending_elicitations_count` as 0 until the server pushes a
 * `pending_elicitations_updated_at` strictly newer than `serverTime`.
 */
export function recordApproval(sessionId: string, serverTime: number): void {
  approvalTimes.set(sessionId, serverTime);
}

/**
 * Return the effective pending-elicitation count for a session,
 * applying the optimistic override when applicable.
 *
 * @param sessionId - Conversation id.
 * @param count - The `pending_elicitations_count` from the server.
 * @param updatedAt - The `pending_elicitations_updated_at` from the
 *   server (epoch seconds), or `undefined` when the server has no
 *   in-memory timestamp (cross-replica).
 * @returns 0 when an approval override is active, otherwise `count`.
 */
export function effectivePendingCount(
  sessionId: string,
  count: number,
  updatedAt: number | undefined,
): number {
  const approvalTime = approvalTimes.get(sessionId);
  if (approvalTime === undefined) return count;
  // Server has pushed a count newer than our approval → trust it.
  if (updatedAt !== undefined && updatedAt > approvalTime) {
    approvalTimes.delete(sessionId);
    return count;
  }
  // Count predates the approval (or cross-replica with no timestamp) →
  // keep the optimistic zero.
  return 0;
}

/** Clear all overrides (test helper). */
export function clearApprovalOverrides(): void {
  approvalTimes.clear();
}
