import { Pmv2ChatProvider } from "./pmv2/Pmv2ChatContext";
import { Pmv2ChatSidebar } from "./Pmv2ChatSidebar";
import { Pmv2Board } from "./pmv2/Pmv2Board";

/**
 * Self-contained layout for `/pmv2`. Mirrors the AppShell "chat +
 * workspace group" pattern (main surface + right rail) but owns its own chrome
 * instead of nesting inside the session ChatHeader / WorkspacePanel wrapper.
 */
export function Pmv2Shell() {
  return (
    <Pmv2ChatProvider>
      <div className="pmv2-shell grid h-full w-full min-w-0 flex-1" data-testid="pmv2-page">
        <div
          className="min-h-0 min-w-0 bg-white"
          data-testid="pmv2-board"
          aria-label="GlobalHub board"
        >
          <Pmv2Board />
        </div>
        <Pmv2ChatSidebar />
      </div>
    </Pmv2ChatProvider>
  );
}
