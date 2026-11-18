import { useParams } from "@/lib/routing";
import { SystemNoticeDot } from "./SystemNoticeDot";
import { useHasWorkerUpdates } from "./useWorkerUpdates";

/**
 * The dot beside the Global Hub link. It shows while a worker has a finished
 * turn that the user has not seen, and otherwise while the board has a notice
 * that the user has not seen. The session open on its page does not count.
 */
export function GlobalHubNavDot() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const hasWorkerUpdates = useHasWorkerUpdates(conversationId);
  if (!hasWorkerUpdates) return <SystemNoticeDot />;
  return (
    <span
      role="img"
      aria-label="New worker messages"
      title="A worker on the Global Hub board has new messages"
      className="ml-auto size-1.5 shrink-0 rounded-full bg-brand-accent"
      data-testid="globalhub-worker-dot"
    />
  );
}
