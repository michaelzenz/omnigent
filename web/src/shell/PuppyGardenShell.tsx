import { PuppyGardenChatSidebar } from "./PuppyGardenChatSidebar";

/**
 * Self-contained layout for `/puppy-garden`. Mirrors the AppShell "chat +
 * workspace group" pattern (main surface + right rail) but owns its own chrome
 * instead of nesting inside the session ChatHeader / WorkspacePanel wrapper.
 */
export function PuppyGardenShell() {
  return (
    <div
      className="grid h-full w-full min-w-0 flex-1 grid-cols-[minmax(0,1fr)_min(420px,40vw)]"
      data-testid="puppy-garden-page"
    >
      <div
        className="min-h-0 min-w-0 bg-white"
        data-testid="puppy-garden-board"
        aria-label="PuppyGarden board"
      />
      <PuppyGardenChatSidebar />
    </div>
  );
}
