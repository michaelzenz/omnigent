import { useState } from "react";
import { TimezonePicker } from "@/components/scheduled/TimezonePicker";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  type GlobalHubSettings,
  type GlobalHubSettingsUpdate,
  useSaveGlobalHubSettings,
} from "@/hooks/useGlobalHubSettings";
import { useHosts } from "@/hooks/useHosts";
import { useRecentWorkspaces } from "@/hooks/useRecentWorkspaces";
import { localTimezone } from "@/lib/timezones";
import { HostLabel } from "@/shell/HostLabel";
import { WorkspacePathField } from "@/shell/WorkspacePathField";
import { WorkspacePickerDialog } from "@/shell/WorkspacePickerDialog";

const DEFAULT_SWEEP_TIME = "09:00";
// The server accepts a time of day on a 24-hour clock.
const SWEEP_TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

interface GlobalHubSettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The saved setting the form starts from; `undefined` while it loads. */
  settings: GlobalHubSettings | undefined;
}

/**
 * Choose the host and workspace where the broker, custodian and managers run
 * their sessions. The server checks that the host is online and the folder
 * exists, and refuses the save otherwise. The form also sets when the
 * custodian looks for sessions to clean up each day.
 */
export function GlobalHubSettingsDialog({
  open,
  onOpenChange,
  settings,
}: GlobalHubSettingsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="globalhub-settings-dialog"
        className="flex flex-col gap-4 sm:max-w-lg"
      >
        {open && <SettingsForm settings={settings} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

function SettingsForm({
  settings,
  onDone,
}: {
  settings: GlobalHubSettings | undefined;
  onDone: () => void;
}) {
  const [hostId, setHostId] = useState(settings?.host_id ?? "");
  const [workspace, setWorkspace] = useState(settings?.workspace ?? "");
  const [reportNewSessions, setReportNewSessions] = useState(settings?.report_new_sessions ?? true);
  const savedSweep = settings?.custodian_sweep;
  const [sweepEnabled, setSweepEnabled] = useState(savedSweep?.enabled ?? true);
  const [sweepTime, setSweepTime] = useState(savedSweep?.time ?? DEFAULT_SWEEP_TIME);
  const [sweepTimezone, setSweepTimezone] = useState(() => savedSweep?.timezone ?? localTimezone());
  const [browsing, setBrowsing] = useState(false);
  const hosts = useHosts();
  const { recent, addRecent } = useRecentWorkspaces(hostId || null);
  const save = useSaveGlobalHubSettings();
  const path = workspace.trim();
  const sweep = SWEEP_TIME.test(sweepTime)
    ? { enabled: sweepEnabled, time: sweepTime, timezone: sweepTimezone }
    : undefined;

  function submit(placement: Pick<GlobalHubSettingsUpdate, "host_id" | "workspace">): void {
    save.mutate(
      { ...placement, report_new_sessions: reportNewSessions, custodian_sweep: sweep },
      {
        onSuccess: (saved) => {
          if (saved.workspace !== null) addRecent(saved.workspace);
          onDone();
        },
      },
    );
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Global Hub settings</DialogTitle>
        <DialogDescription>
          Your broker, custodian and managers run their sessions on this host, in this workspace.
        </DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium text-muted-foreground">Host</span>
        <Select
          value={hostId}
          onValueChange={(next) => {
            setHostId(next);
            // A folder on one host means nothing on another.
            setWorkspace("");
          }}
          componentId="globalhub_settings.host"
        >
          <SelectTrigger className="w-full text-xs" data-testid="globalhub-host-select">
            <SelectValue placeholder="Select a host" />
          </SelectTrigger>
          <SelectContent>
            {(hosts.data ?? []).map((host) => (
              <SelectItem
                key={host.host_id}
                value={host.host_id}
                disabled={host.status !== "online"}
                data-testid={`globalhub-host-option-${host.host_id}`}
              >
                <HostLabel host={host} />
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium text-muted-foreground">Workspace</span>
        {hostId ? (
          <>
            <WorkspacePathField
              hostId={hostId}
              value={workspace}
              onChange={setWorkspace}
              onBrowse={() => setBrowsing((value) => !value)}
              onCommit={(typed) => {
                setWorkspace(typed);
                setBrowsing(true);
              }}
              recent={recent}
              dropdownDisabled={browsing}
            />
            <WorkspacePickerDialog
              open={browsing}
              onOpenChange={setBrowsing}
              hostId={hostId}
              initialPath={path}
              onConfirm={setWorkspace}
            />
          </>
        ) : (
          <p className="text-xs text-muted-foreground">Select a host to choose a workspace.</p>
        )}
      </div>
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted-foreground">New sessions</span>
          <span className="text-xs text-muted-foreground">
            Send the finished turns of sessions on no task to the broker, which can add them to a
            task. Sessions on a task always update their managers.
          </span>
        </div>
        <Switch
          aria-label="Send new sessions to the broker"
          checked={reportNewSessions}
          onCheckedChange={setReportNewSessions}
        />
      </div>
      <div className="flex flex-col gap-2">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">Session cleanup</span>
            <span className="text-xs text-muted-foreground">
              Every day at this time, the custodian looks for sessions that are safe to delete and
              asks you on the board before it deletes any.
            </span>
          </div>
          <Switch
            aria-label="Daily session cleanup"
            checked={sweepEnabled}
            onCheckedChange={setSweepEnabled}
          />
        </div>
        {sweepEnabled && (
          <div className="flex items-center gap-2">
            <Input
              type="time"
              aria-label="Cleanup time"
              aria-invalid={sweep === undefined ? true : undefined}
              value={sweepTime}
              onChange={(event) => setSweepTime(event.target.value)}
              className="w-32 text-xs"
            />
            <TimezonePicker
              timezone={sweepTimezone}
              onChange={setSweepTimezone}
              className="min-w-0 flex-1 justify-between text-xs font-normal"
              testId="globalhub-sweep-timezone"
            />
          </div>
        )}
      </div>
      {save.error && (
        <p role="alert" className="text-xs text-destructive" data-testid="globalhub-settings-error">
          {save.error.message}
        </p>
      )}
      <DialogFooter>
        {settings?.host_id != null && (
          <Button
            variant="ghost"
            disabled={save.isPending}
            onClick={() => submit({ host_id: null, workspace: null })}
            data-testid="globalhub-settings-clear"
          >
            Clear
          </Button>
        )}
        <Button
          disabled={!hostId || !path || sweep === undefined}
          loading={save.isPending}
          onClick={() => submit({ host_id: hostId, workspace: path })}
          data-testid="globalhub-settings-save"
        >
          Save
        </Button>
      </DialogFooter>
    </>
  );
}
