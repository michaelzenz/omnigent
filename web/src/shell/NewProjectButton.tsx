import { type CSSProperties, useRef, useState } from "react";
import { ChevronDownIcon, PlusIcon, SmilePlusIcon } from "lucide-react";
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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { EmojiPicker } from "@/components/ProjectIconPicker";
import { useCreateProject } from "@/hooks/useConversations";
import { useHosts } from "@/hooks/useHosts";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { sandboxOptionLabel } from "@/lib/capabilities";
import { SANDBOX_HOST_CHOICE } from "@/lib/hostPreferences";
import type { ProjectConfig } from "@/lib/projectsApi";
import { shouldGuardDialogDismiss } from "@/lib/dialogDismissGuard";
import { isNavigablePath, WorkspacePicker } from "./WorkspacePicker";
import { cn } from "@/lib/utils";

/** Select sentinel for "no default" — Radix Select can't hold an empty value. */
const NONE = "__none__";

/**
 * "New project" control in the Projects group header. Opens a dialog that
 * creates an EMPTY first-class project (`POST /v1/projects`) — the capability
 * the legacy label model can't express. On success the new folder is expanded
 * (via `onCreated`) so the user can immediately file sessions into it.
 *
 * Host and working directory are optional defaults (soft hints, same as the
 * project settings dialog): a workspace is host-relative and only persisted
 * alongside a concrete host.
 */
export function NewProjectButton({ onCreated }: { onCreated: (name: string) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [icon, setIcon] = useState<string | undefined>(undefined);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const [hostId, setHostId] = useState<string>(NONE);
  const [workspace, setWorkspace] = useState("");
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const createProject = useCreateProject();
  // Only fetch hosts when the dialog is open — avoids an extra observer
  // (and re-renders on hosts changes) while the button sits idle.
  const hosts = useHosts({ enabled: open });
  const info = useServerInfo();
  const managedSandboxesEnabled = info !== "loading" && info.managed_sandboxes_enabled;
  const sandboxProvider = info !== "loading" ? info.sandbox_provider : null;

  // Dropdown dismiss guard — a nested Select / workspace browser portals
  // outside DialogContent, so their dismiss can close the whole modal.
  const dropdownOpenCountRef = useRef(0);
  const dropdownClosedAtRef = useRef(0);
  const onDropdownOpenChange = (isOpen: boolean) => {
    if (isOpen) {
      dropdownOpenCountRef.current += 1;
    } else {
      dropdownOpenCountRef.current = Math.max(0, dropdownOpenCountRef.current - 1);
      dropdownClosedAtRef.current = Date.now();
    }
  };
  const guardDialogDismiss = (event: {
    target: EventTarget | null;
    preventDefault: () => void;
  }) => {
    if (
      shouldGuardDialogDismiss(event.target, {
        selectOpen: dropdownOpenCountRef.current > 0,
        msSinceSelectClose: Date.now() - dropdownClosedAtRef.current,
      })
    ) {
      event.preventDefault();
    }
  };

  const reset = () => {
    setName("");
    setIcon(undefined);
    setHostId(NONE);
    setWorkspace("");
    setWorkspaceOpen(false);
  };

  const submit = () => {
    const trimmed = name.trim();
    if (trimmed === "") return;
    // Build config from set fields only — unset slots are absent keys.
    const config: ProjectConfig = {};
    if (icon) config.icon = icon;
    if (hostId !== NONE) config.host_id = hostId;
    // Workspace is host-relative: only persist with a concrete host (not
    // sandbox — a sandbox create provisions its own workspace).
    const ws =
      hostId !== NONE && hostId !== SANDBOX_HOST_CHOICE ? workspace.trim() || undefined : undefined;
    if (ws) config.workspace = ws;
    createProject.mutate(
      { name: trimmed, config: Object.keys(config).length ? config : undefined },
      {
        onSuccess: (project) => {
          setOpen(false);
          reset();
          onCreated(project.name);
        },
      },
    );
  };

  // Online hosts + sandbox (when available).
  const onlineHosts = (hosts.data ?? []).filter((h) => h.status === "online");
  const browsableHostId = hostId !== NONE && hostId !== SANDBOX_HOST_CHOICE ? hostId : null;

  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="New project"
            data-testid="new-project"
            className="text-muted-foreground"
            onClick={(e) => {
              e.stopPropagation();
              reset();
              setOpen(true);
            }}
          >
            <PlusIcon className="size-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">New project</TooltipContent>
      </Tooltip>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          onClick={(e) => e.stopPropagation()}
          onPointerDownCapture={(e) => {
            if (!emojiOpen) return;
            const target = e.target as Element;
            if (
              target.closest('[data-slot="popover-content"]') ||
              target.closest('[data-testid="new-project-icon"]')
            )
              return;
            setEmojiOpen(false);
          }}
          onPointerDownOutside={guardDialogDismiss}
          onInteractOutside={guardDialogDismiss}
        >
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
            <DialogDescription>
              Create an empty project, then file sessions into it from a session's menu. Host and
              working directory are optional defaults for new sessions.
            </DialogDescription>
          </DialogHeader>
          {/* Name + icon row */}
          <div className="flex items-stretch overflow-hidden rounded-lg border border-input">
            <Popover open={emojiOpen} onOpenChange={setEmojiOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  aria-label="Choose project icon"
                  data-testid="new-project-icon"
                  className={cn(
                    "flex size-[38px] shrink-0 cursor-pointer items-center justify-center outline-none transition-colors",
                    icon ? "bg-muted" : "bg-tag-pink",
                  )}
                >
                  {icon ? (
                    <span className="text-xl leading-none">{icon}</span>
                  ) : (
                    <SmilePlusIcon className="size-4 text-brand-accent" />
                  )}
                </button>
              </PopoverTrigger>
              <PopoverContent
                align="start"
                collisionPadding={8}
                style={
                  {
                    "--emoji-picker-height":
                      "min(420px, var(--radix-popover-content-available-height))",
                  } as CSSProperties
                }
                className="emoji-picker-popover flex max-h-[var(--radix-popover-content-available-height)] w-auto flex-col overflow-hidden p-0"
                onWheel={(e) => e.stopPropagation()}
                onInteractOutside={() => setEmojiOpen(false)}
              >
                <EmojiPicker
                  onSelect={(native) => {
                    setIcon(native);
                    setEmojiOpen(false);
                  }}
                />
              </PopoverContent>
            </Popover>
            <input
              autoFocus
              className="w-full bg-transparent px-3 py-2 text-ui outline-none"
              placeholder="Project name…"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submit();
                }
              }}
            />
          </div>

          {/* Host — optional default for new sessions */}
          <div className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
            <label className="flex flex-col pt-1.5">
              <span className="text-ui font-medium">Host</span>
              <span className="text-sm text-muted-foreground">
                Where new sessions run by default
              </span>
            </label>
            <div className="sm:w-64">
              <Select value={hostId} onValueChange={setHostId} onOpenChange={onDropdownOpenChange}>
                <SelectTrigger className="w-full" data-testid="new-project-host">
                  <SelectValue placeholder="No default" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>No default</SelectItem>
                  {managedSandboxesEnabled && (
                    <SelectItem value={SANDBOX_HOST_CHOICE}>
                      {sandboxOptionLabel(sandboxProvider)}
                    </SelectItem>
                  )}
                  {onlineHosts.map((h) => (
                    <SelectItem key={h.host_id} value={h.host_id}>
                      {h.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Working directory — host-relative, only with a concrete host */}
          <div className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
            <label htmlFor="new-project-workspace" className="flex flex-col pt-1.5">
              <span className="text-ui font-medium">Working directory</span>
              <span className="text-sm text-muted-foreground">
                {hostId === NONE
                  ? "Pick a host first"
                  : browsableHostId
                    ? "Browse the host or type a path"
                    : "Absolute path on the host"}
              </span>
            </label>
            <div className="sm:w-64" data-testid="new-project-workspace">
              {hostId === NONE ? (
                <p className="rounded-md border border-dashed px-3 py-2 text-ui text-muted-foreground">
                  Pick a host first
                </p>
              ) : browsableHostId ? (
                <div className="relative flex flex-col gap-1.5">
                  <button
                    type="button"
                    onClick={() => setWorkspaceOpen((v) => !v)}
                    aria-expanded={workspaceOpen}
                    className="flex h-8 w-full items-center justify-between gap-2 rounded-md border border-input bg-transparent px-3 text-ui outline-none"
                  >
                    <span className={workspace ? "truncate" : "truncate text-muted-foreground"}>
                      {workspace || "Browse…"}
                    </span>
                    <ChevronDownIcon
                      className={`size-4 shrink-0 opacity-50 transition-transform ${
                        workspaceOpen ? "rotate-180" : ""
                      }`}
                    />
                  </button>
                  {workspaceOpen && (
                    <>
                      <button
                        type="button"
                        aria-label="Close directory browser"
                        className="fixed inset-0 z-10 cursor-default"
                        onClick={() => setWorkspaceOpen(false)}
                      />
                      <div className="absolute right-0 left-0 top-full z-20 mt-1 rounded-[12px] border border-border bg-popover p-2 shadow-menu [&>[data-testid=workspace-picker]]:border-0">
                        <WorkspacePicker
                          hostId={browsableHostId}
                          initialPath={isNavigablePath(workspace) ? workspace : undefined}
                          onNavigate={setWorkspace}
                        />
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <input
                  id="new-project-workspace"
                  className="w-full rounded-md border bg-transparent px-3 py-2 text-ui outline-none"
                  placeholder="/path/to/repo"
                  value={workspace}
                  onChange={(e) => setWorkspace(e.target.value)}
                />
              )}
            </div>
          </div>

          {createProject.isError && (
            <p className="text-ui text-destructive" role="alert">
              {(createProject.error as Error).message}
            </p>
          )}
          <DialogFooter className="border-t-0 bg-transparent">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setOpen(false)}
              disabled={createProject.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              data-testid="new-project-confirm"
              loading={createProject.isPending}
              disabled={name.trim() === ""}
              onClick={submit}
            >
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
