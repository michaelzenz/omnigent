import { useRef, useState } from "react";
import { SettingsIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { GlobalHubSettingsDialog } from "@/globalhub/GlobalHubSettingsDialog";
import { SystemNoticeCard } from "@/globalhub/SystemNoticeCard";
import { TaskBoard } from "@/globalhub/TaskBoard";
import { useGlobalHubSettings } from "@/hooks/useGlobalHubSettings";

/** The Global Hub board, with its settings behind the gear in the header. */
export function GlobalHubPage() {
  const settings = useGlobalHubSettings();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const boardRef = useRef<HTMLDivElement>(null);
  const openSettings = () => setSettingsOpen(true);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col" data-testid="globalhub-page">
      <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <h1 className="text-lg font-semibold">Global Hub</h1>
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
      <div ref={boardRef} className="min-h-0 flex-1 overflow-auto" data-testid="globalhub-board">
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
            <SystemNoticeCard />
            <TaskBoard scrollRoot={boardRef} />
          </>
        )}
      </div>
      <GlobalHubSettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        settings={settings.data}
      />
    </div>
  );
}
