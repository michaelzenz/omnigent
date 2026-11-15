import { useSeenSystemNoticeId, useSystemNotice } from "@/hooks/useGlobalHubNotice";

/** A dot beside the Global Hub link while the board has a notice the user has not seen. */
export function SystemNoticeDot() {
  const notice = useSystemNotice().data;
  const seenNoticeId = useSeenSystemNoticeId();
  if (!notice || notice.id === seenNoticeId) return null;
  return (
    <span
      role="img"
      aria-label="New system notice"
      title="A system notice is pinned on the Global Hub board"
      className="ml-auto size-1.5 shrink-0 rounded-full bg-brand-accent"
      data-testid="globalhub-notice-dot"
    />
  );
}
