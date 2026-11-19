import { useRef, useState } from "react";
import { SearchIcon, SettingsIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { BoardConfigButton } from "@/globalhub/BoardConfigDialog";
import { FyiStream } from "@/globalhub/FyiStream";
import { GlobalHubChatProvider } from "@/globalhub/GlobalHubChat";
import { GlobalHubChatPanel } from "@/globalhub/GlobalHubChatPanel";
import { GlobalHubSettingsDialog } from "@/globalhub/GlobalHubSettingsDialog";
import { PriorityFilter } from "@/globalhub/PriorityFilter";
import { SystemNoticeCard } from "@/globalhub/SystemNoticeCard";
import { TaskBoard } from "@/globalhub/TaskBoard";
import { usePriorityFilter } from "@/globalhub/usePriorityFilter";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useGlobalHubSettings } from "@/hooks/useGlobalHubSettings";
import { useBoardPriorityCounts } from "@/hooks/useGlobalHubTasks";

// The search waits for a pause in typing before it asks the server.
const SEARCH_DELAY_MS = 300;

/**
 * The Global Hub board, with its priority filter, search and settings in the
 * header. A manager's or worker's chat opens in a panel over the right side of
 * the board.
 */
export function GlobalHubPage() {
  const settings = useGlobalHubSettings();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const query = useDebouncedValue(search.trim(), SEARCH_DELAY_MS);
  const [priority, setPriority] = usePriorityFilter();
  const priorityCounts = useBoardPriorityCounts(query, priority);
  const boardRef = useRef<HTMLDivElement>(null);
  const openSettings = () => setSettingsOpen(true);

  return (
    <GlobalHubChatProvider>
      <div
        className="globalhub-page flex min-h-0 min-w-0 flex-1 flex-col pt-14 md:pt-12"
        data-testid="globalhub-page"
      >
        <div className="globalhub-header flex h-12 items-center justify-between gap-3 border-b px-4">
          <div className="flex min-w-0 items-center gap-3">
            <h1 className="shrink-0 text-lg font-semibold">Global Hub</h1>
            <PriorityFilter value={priority} counts={priorityCounts} onChange={setPriority} />
          </div>
          <div className="flex min-w-0 items-center gap-1">
            <div className="relative w-64 min-w-0">
              <SearchIcon
                className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setSearch("");
                }}
                placeholder="Search tasks"
                aria-label="Search tasks"
                className="h-8 pr-8 pl-8"
                data-testid="globalhub-search"
              />
              {search && (
                <button
                  type="button"
                  aria-label="Clear search"
                  className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
                  onClick={() => setSearch("")}
                >
                  <XIcon className="size-4" aria-hidden />
                </button>
              )}
            </div>
            <BoardConfigButton />
            <Button
              variant="ghost"
              size="icon"
              aria-label="Global Hub settings"
              title="Global Hub settings"
              onClick={openSettings}
              data-testid="globalhub-settings-button"
            >
              <SettingsIcon className="size-4" />
            </Button>
          </div>
        </div>
        <div className="relative min-h-0 flex-1">
          <div ref={boardRef} className="h-full overflow-auto" data-testid="globalhub-board">
            {settings.isPending ? (
              <div className="flex justify-center p-8">
                <Spinner className="size-5 text-muted-foreground" aria-label="Loading" />
              </div>
            ) : settings.isError ? (
              <p role="alert" className="p-8 text-center text-sm text-destructive">
                Could not load the Global Hub settings: {settings.error.message}
              </p>
            ) : (
              <>
                {settings.data.host_id === null && (
                  <div
                    className="flex flex-col items-center gap-3 p-8 text-center"
                    data-testid="globalhub-setup-prompt"
                  >
                    <p className="text-sm text-muted-foreground">
                      Choose a host and workspace to start.
                    </p>
                    <Button onClick={openSettings} data-testid="globalhub-setup-button">
                      Choose host and workspace
                    </Button>
                  </div>
                )}
                {!query && (
                  <>
                    <SystemNoticeCard />
                    <FyiStream />
                  </>
                )}
                <TaskBoard scrollRoot={boardRef} query={query} priority={priority} />
              </>
            )}
          </div>
          <GlobalHubChatPanel />
        </div>
        <GlobalHubSettingsDialog
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          settings={settings.data}
        />
      </div>
    </GlobalHubChatProvider>
  );
}
