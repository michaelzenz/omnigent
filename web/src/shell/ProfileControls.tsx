import { useEffect, useMemo, useState } from "react";
import {
  ChevronDownIcon,
  Loader2Icon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
  Trash2Icon,
} from "lucide-react";
import {
  type PromptProfile,
  useDeletePromptProfile,
  useCreatePromptProfile,
  usePromptProfiles,
  useUpdatePromptProfile,
} from "@/hooks/usePromptProfiles";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { CreateAgentDialog } from "./CreateAgentDialog";
import type { AgentBundleInput } from "@/lib/agentBundle";
import { PromptProfileSelect } from "@/components/HarnessConfigControls";
import { useOmniHarnessSettings, useUpdateOmniHarnessSettings } from "@/hooks/useModelSettings";

export type ProfileSelection = "auto" | string;

export function PromptProfileConfigControl({
  profiles,
  selection,
  onSelect,
  testId,
}: {
  profiles: PromptProfile[];
  selection: ProfileSelection;
  onSelect: (selection: ProfileSelection) => void;
  testId: string;
}) {
  return (
    <div className="flex min-w-0 items-center gap-1">
      <div className="min-w-0 flex-1">
        <PromptProfileSelect
          value={selection}
          onValueChange={onSelect}
          profiles={profiles}
          testId={testId}
        />
      </div>
      <ManageProfilesDialog
        selectedProfileId={selection === "auto" || selection === "auto_include" ? null : selection}
        onSelectProfile={(profile) => onSelect(profile.id)}
        onSelectedProfileRemoved={() => onSelect("auto")}
      />
    </div>
  );
}

export function ProfileControls({
  profiles,
  selection,
  selectedProfileId,
  disabled,
  onSelect,
}: {
  profiles: PromptProfile[];
  selection: ProfileSelection;
  selectedProfileId: string | null;
  disabled: boolean;
  onSelect: (selection: ProfileSelection, profile?: PromptProfile) => void;
}) {
  const selected = profiles.find((profile) => profile.id === selection);
  const label = selection === "auto" ? "Auto Select" : (selected?.name ?? "Auto Select");

  return (
    <div
      className="flex items-center rounded-lg transition-colors hover:bg-muted dark:hover:bg-muted/50"
      data-testid="new-chat-landing-profile-group"
    >
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled}
            className="h-9 gap-1.5 pr-2 pl-2.5 font-normal text-muted-foreground md:h-8"
            data-testid="new-chat-landing-profile-select"
          >
            <span className="max-w-48 truncate text-ui text-foreground">Profile: {label}</span>
            <ChevronDownIcon className="size-3.5 opacity-60" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-64">
          <DropdownMenuItem
            data-testid="new-chat-landing-profile-auto"
            data-active={selection === "auto" ? "true" : undefined}
            onSelect={() => onSelect("auto")}
            className="data-[active=true]:bg-muted"
          >
            Auto Select
          </DropdownMenuItem>
          {profiles.map((profile) => (
            <DropdownMenuItem
              key={profile.id}
              data-testid={`new-chat-landing-profile-${profile.id}`}
              data-active={selection === profile.id ? "true" : undefined}
              onSelect={() => onSelect(profile.id, profile)}
              className="data-[active=true]:bg-muted"
            >
              <span className="truncate">{profile.name}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <span aria-hidden className="h-4 w-px shrink-0 bg-border" />
      <ManageProfilesDialog
        selectedProfileId={selectedProfileId}
        onSelectProfile={(profile) => onSelect(profile.id, profile)}
        onSelectedProfileRemoved={() => onSelect("auto")}
      />
    </div>
  );
}

function ManageProfilesDialog({
  selectedProfileId,
  onSelectProfile,
  onSelectedProfileRemoved,
}: {
  selectedProfileId: string | null;
  onSelectProfile: (profile: PromptProfile) => void;
  onSelectedProfileRemoved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<PromptProfile | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PromptProfile | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [autoIncludeLimit, setAutoIncludeLimit] = useState("5");
  const profilesQuery = usePromptProfiles({ enabled: open });
  const omniHarnessSettings = useOmniHarnessSettings(open);
  const updateOmniHarnessSettings = useUpdateOmniHarnessSettings();
  const deleteProfile = useDeletePromptProfile();
  const create = useCreatePromptProfile();
  const update = useUpdatePromptProfile();

  useEffect(() => {
    if (open && omniHarnessSettings.data) {
      setAutoIncludeLimit(String(omniHarnessSettings.data.promptProfileAutoIncludeLimit));
    }
  }, [open, omniHarnessSettings.data]);

  const rows = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return (profilesQuery.data ?? []).filter(
      (profile) =>
        !query ||
        profile.name.toLocaleLowerCase().includes(query) ||
        profile.description?.toLocaleLowerCase().includes(query),
    );
  }, [profilesQuery.data, search]);

  async function toggle(profile: PromptProfile, enabled: boolean) {
    setActionError(null);
    try {
      await update.mutateAsync({ id: profile.id, enabled });
      if (!enabled && profile.id === selectedProfileId) onSelectedProfileRemoved();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Couldn't update profile");
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setActionError(null);
    try {
      await deleteProfile.mutateAsync(deleteTarget.id);
      if (deleteTarget.id === selectedProfileId) onSelectedProfileRemoved();
      setDeleteTarget(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Couldn't delete profile");
    }
  }

  async function editProfile(input: AgentBundleInput) {
    if (!editTarget) return;
    setActionError(null);
    try {
      const updated = await update.mutateAsync({
        id: editTarget.id,
        name: input.name,
        description: input.description ?? null,
        instructions: input.instructions ?? "",
      });
      if (editTarget.id === selectedProfileId && updated.enabled) onSelectProfile(updated);
      setEditTarget(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Couldn't edit profile");
    }
  }

  async function addProfile(input: AgentBundleInput) {
    setActionError(null);
    try {
      const profile = await create.mutateAsync({
        name: input.name,
        description: input.description ?? null,
        instructions: input.instructions ?? "",
        enabled: true,
      });
      onSelectProfile(profile);
      setCreateOpen(false);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Couldn't add profile");
    }
  }

  async function saveAutoIncludeLimit() {
    setActionError(null);
    const limit = Number.parseInt(autoIncludeLimit, 10);
    if (!Number.isSafeInteger(limit) || limit < 1) {
      setActionError("Auto Include maximum must be a positive whole number");
      return;
    }
    try {
      await updateOmniHarnessSettings.mutateAsync({
        promptProfileAutoIncludeLimit: limit,
      });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Couldn't update Auto Include limit");
    }
  }

  const pendingId = update.isPending
    ? update.variables?.id
    : deleteProfile.isPending
      ? deleteProfile.variables
      : undefined;

  return (
    <>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="size-9 text-muted-foreground md:size-8"
        onClick={() => setOpen(true)}
        data-testid="new-chat-landing-profile-gear"
      >
        <SettingsIcon className="size-4" />
        <span className="sr-only">Manage profiles</span>
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="flex max-h-[85vh] w-[calc(100vw-2rem)] flex-col sm:max-w-4xl"
          data-testid="manage-profiles-dialog"
        >
          <DialogHeader>
            <DialogTitle>Manage profiles</DialogTitle>
            <DialogDescription>
              Create and manage prompt profiles for Omnigent chats.
            </DialogDescription>
          </DialogHeader>
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
              <div>
                <div className="text-sm font-medium">Auto Include maximum</div>
                <div className="text-sm text-muted-foreground">
                  Maximum suitable profiles injected into one turn
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  value={autoIncludeLimit}
                  onChange={(event) => {
                    if (/^\d*$/.test(event.target.value)) setAutoIncludeLimit(event.target.value);
                  }}
                  className="w-20"
                  aria-label="Auto Include maximum"
                  data-testid="manage-profiles-auto-include-limit"
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void saveAutoIncludeLimit()}
                  disabled={updateOmniHarnessSettings.isPending}
                  data-testid="manage-profiles-auto-include-save"
                >
                  Save
                </Button>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <SearchIcon className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search profiles"
                  className="pl-9"
                  data-testid="manage-profiles-search"
                />
              </div>
              <Button
                type="button"
                onClick={() => setCreateOpen(true)}
                data-testid="manage-profiles-add"
              >
                <PlusIcon className="size-4" />
                Add profile
              </Button>
            </div>
            {actionError && (
              <p
                role="alert"
                className="text-sm text-destructive"
                data-testid="manage-profiles-error"
              >
                {actionError}
              </p>
            )}
            <div className="min-h-0 overflow-y-auto rounded-lg border">
              {profilesQuery.isLoading ? (
                <div className="flex items-center justify-center gap-2 p-10 text-muted-foreground">
                  <Loader2Icon className="size-4 animate-spin" />
                  Loading profiles…
                </div>
              ) : profilesQuery.isError ? (
                <div className="p-10 text-center text-destructive">
                  Couldn't load profiles. Close and reopen to try again.
                </div>
              ) : rows.length === 0 ? (
                <div className="p-10 text-center text-muted-foreground">No profiles found.</div>
              ) : (
                <div className="divide-y">
                  {rows.map((profile) => (
                    <div
                      key={profile.id}
                      className="grid grid-cols-[minmax(12rem,1fr)_auto] items-center gap-4 p-4"
                      data-testid={`manage-profile-row-${profile.id}`}
                    >
                      <div className="min-w-0">
                        <span className="truncate font-medium">{profile.name}</span>
                        <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                          {profile.description || "No description"}
                        </p>
                      </div>
                      <div className="flex items-center gap-3">
                        {pendingId === profile.id && (
                          <Loader2Icon
                            className="size-4 animate-spin text-muted-foreground"
                            data-testid={`manage-profile-pending-${profile.id}`}
                          />
                        )}
                        <label className="flex items-center gap-2 text-sm text-muted-foreground">
                          <span>Enabled</span>
                          <Switch
                            checked={profile.enabled}
                            disabled={pendingId === profile.id}
                            onCheckedChange={(enabled) => void toggle(profile, enabled)}
                            aria-label={`Enabled ${profile.name}`}
                            data-testid={`manage-profile-enabled-${profile.id}`}
                          />
                        </label>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => setEditTarget(profile)}
                          aria-label={`Edit ${profile.name}`}
                          data-testid={`manage-profile-edit-${profile.id}`}
                        >
                          <PencilIcon className="size-4" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => setDeleteTarget(profile)}
                          aria-label={`Delete ${profile.name}`}
                          data-testid={`manage-profile-delete-${profile.id}`}
                        >
                          <Trash2Icon className="size-4 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={deleteTarget !== null} onOpenChange={(next) => !next && setDeleteTarget(null)}>
        <DialogContent className="sm:max-w-md" data-testid="manage-profile-delete-confirm">
          <DialogHeader>
            <DialogTitle>Delete profile?</DialogTitle>
            <DialogDescription>
              {deleteTarget?.name} will be permanently deleted and removed from Profile selection.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void confirmDelete()}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <CreateAgentDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreate={(input) => void addProfile(input)}
        showMcpServers={false}
        showHarnessModel={false}
        title="Add profile"
        submitLabel={create.isPending ? "Adding…" : "Add profile"}
      />
      <CreateAgentDialog
        open={editTarget !== null}
        onOpenChange={(next) => !next && setEditTarget(null)}
        onCreate={(input) => void editProfile(input)}
        initialValue={
          editTarget
            ? {
                name: editTarget.name,
                description: editTarget.description ?? undefined,
                instructions: editTarget.instructions,
              }
            : undefined
        }
        showMcpServers={false}
        showHarnessModel={false}
        title="Edit profile"
        submitLabel={update.isPending ? "Saving…" : "Save changes"}
      />
    </>
  );
}
