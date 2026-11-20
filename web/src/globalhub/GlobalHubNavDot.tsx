import { useBoardHasUpdates } from "@/hooks/useGlobalHubTasks";
import { SystemNoticeDot } from "./SystemNoticeDot";

/**
 * The dot beside the Global Hub link. It shows while a task on the board has an
 * update that the user has not cleared, and otherwise while the board has a
 * notice that the user has not seen.
 */
export function GlobalHubNavDot() {
  const hasUpdates = useBoardHasUpdates();
  if (!hasUpdates) return <SystemNoticeDot />;
  return (
    <span
      role="img"
      aria-label="Tasks with updates"
      title="A task on the Global Hub board has an update"
      className="ml-auto size-1.5 shrink-0 rounded-full bg-brand-accent"
      data-testid="globalhub-update-dot"
    />
  );
}
