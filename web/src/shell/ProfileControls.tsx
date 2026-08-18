import { useMemo, useState } from "react";
import {
  ChevronDownIcon,
  Loader2Icon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
  Trash2Icon,
} from "lucide-react";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import {
  useArchiveProfile,
  useCreateProfile,
  useEditProfile,
  useProfiles,
  useUpdateProfileEnabled,
} from "@/hooks/useProfiles";
import { buildAgentBundle, type AgentBundleInput } from "@/lib/agentBundle";
import { isNativeCodingAgent } from "@/lib/nativeCodingAgents";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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

export type ProfileSelection = "auto" | string;

export function ProfileControls({
  profiles,
  selection,
  resolvedAutoProfile,
  selectedAgentId,
  disabled,
  onSelect,
}: {
  profiles: AvailableAgent[];
  selection: ProfileSelection;
  resolvedAutoProfile: AvailableAgent | null;
  selectedAgentId: string | null;
  disabled: boolean;
  onSelect: (selection: ProfileSelection, profile?: AvailableAgent) => void;
}) {
  const selected =
    profiles.find((profile) => profile.id === selection) ??
    (resolvedAutoProfile?.id === selection ? resolvedAutoProfile : undefined);
  const label = selection === "auto" ? "Auto Select" : (selected?.display_name ?? "Auto Select");

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
              <span className="truncate">{profile.display_name}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <span aria-hidden className="h-4 w-px shrink-0 bg-border" />
      <ManageProfilesDialog
        selectedAgentId={selectedAgentId}
        onSelectProfile={(profile) => onSelect(profile.id, profile)}
        onSelectedProfileRemoved={() => onSelect("auto")}
      />
    </div>
  );
}

function ManageProfilesDialog({
  selectedAgentId,
  onSelectProfile,
  onSelectedProfileRemoved,
}: {
  selectedAgentId: string | null;
  onSelectProfile: (profile: AvailableAgent) => void;
  onSelectedProfileRemoved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<AvailableAgent | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AvailableAgent | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const profilesQuery = useProfiles({ enabled: open });
  const updateEnabled = useUpdateProfileEnabled();
  const archive = useArchiveProfile();
  const create = useCreateProfile();
  const edit = useEditProfile();

  const managedProfiles = useMemo(
    () =>
      (profilesQuery.data ?? []).filter(
        (profile) =>
          !profile.archived && profile.name !== "omnigent" && !isNativeCodingAgent(profile),
      ),
    [profilesQuery.data],
  );
  const rows = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return managedProfiles.filter(
      (profile) =>
        !query ||
        profile.display_name.toLocaleLowerCase().includes(query) ||
        profile.description?.toLocaleLowerCase().includes(query),
    );
  }, [managedProfiles, search]);
  const enabledCount = managedProfiles.filter((profile) => profile.enabled !== false).length;

  async function toggle(profile: AvailableAgent, enabled: boolean) {
    setActionError(null);
    try {
      await updateEnabled.mutateAsync({ id: profile.id, enabled });
      if (!enabled && profile.id === selectedAgentId) onSelectedProfileRemoved();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Couldn't update profile");
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setActionError(null);
    try {
      await archive.mutateAsync(deleteTarget.id);
      if (deleteTarget.id === selectedAgentId) onSelectedProfileRemoved();
      setDeleteTarget(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Couldn't delete profile");
    }
  }

  async function editProfile(input: AgentBundleInput) {
    if (!editTarget) return;
    setActionError(null);
    try {
      const updated = await edit.mutateAsync({
        id: editTarget.id,
        name: input.name,
        description: input.description,
        instructions: input.instructions,
      });
      if (editTarget.id === selectedAgentId) onSelectProfile(updated);
      setEditTarget(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Couldn't edit profile");
    }
  }

  async function addProfile(input: AgentBundleInput) {
    setActionError(null);
    try {
      const bundle = await buildAgentBundle(input);
      const profile = await create.mutateAsync(bundle);
      onSelectProfile(profile);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Couldn't add profile");
    }
  }

  const pendingId = updateEnabled.isPending
    ? updateEnabled.variables?.id
    : archive.isPending
      ? archive.variables
      : edit.isPending
        ? edit.variables?.id
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
          className="flex max-h-[85vh] w-[calc(100vw-2rem)] flex-col sm:max-w-6xl"
          data-testid="manage-profiles-dialog"
        >
          <DialogHeader>
            <DialogTitle>Manage profiles</DialogTitle>
            <DialogDescription>
              Create and manage prompt profiles for Omnigent chats.
            </DialogDescription>
          </DialogHeader>
          <div className="flex min-h-0 flex-1 flex-col gap-3">
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
                      className="grid grid-cols-[minmax(12rem,2fr)_minmax(10rem,1fr)_minmax(10rem,1fr)_auto] items-center gap-4 p-4"
                      data-testid={`manage-profile-row-${profile.id}`}
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="truncate font-medium">{profile.display_name}</span>
                          <Badge variant="outline">{profile.builtin ? "Built-in" : "Custom"}</Badge>
                        </div>
                        <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                          {profile.description || "No description"}
                        </p>
                      </div>
                      <div className="text-sm">
                        {profile.is_multi_agent
                          ? `Multi-agent · ${profile.subagent_count ?? 0} sub-agents`
                          : "Single agent"}
                      </div>
                      <div className="min-w-0 text-sm text-muted-foreground">
                        <div className="truncate">
                          Harness: {profile.default_harness || "Default"}
                        </div>
                        <div className="truncate">Model: {profile.default_model || "Default"}</div>
                      </div>
                      <div className="flex items-center gap-3">
                        {pendingId === profile.id && (
                          <Loader2Icon
                            className="size-4 animate-spin text-muted-foreground"
                            data-testid={`manage-profile-pending-${profile.id}`}
                          />
                        )}
                        <Switch
                          checked={profile.enabled !== false}
                          disabled={
                            pendingId === profile.id ||
                            (profile.enabled !== false && enabledCount === 1)
                          }
                          onCheckedChange={(enabled) => void toggle(profile, enabled)}
                          aria-label={`Enable ${profile.display_name}`}
                          data-testid={`manage-profile-enabled-${profile.id}`}
                        />
                        {!profile.builtin && (
                          <>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              onClick={() => setEditTarget(profile)}
                              aria-label={`Edit ${profile.display_name}`}
                              data-testid={`manage-profile-edit-${profile.id}`}
                            >
                              <PencilIcon className="size-4" />
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              disabled={managedProfiles.length === 1}
                              title={
                                managedProfiles.length === 1
                                  ? "The last profile cannot be deleted"
                                  : undefined
                              }
                              onClick={() => setDeleteTarget(profile)}
                              aria-label={`Delete ${profile.display_name}`}
                              data-testid={`manage-profile-delete-${profile.id}`}
                            >
                              <Trash2Icon className="size-4 text-destructive" />
                            </Button>
                          </>
                        )}
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
              {deleteTarget?.display_name} will be archived and removed from Profile selection.
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
                instructions: editTarget.instructions ?? undefined,
              }
            : undefined
        }
        showMcpServers={false}
        title="Edit profile"
        submitLabel={edit.isPending ? "Saving…" : "Save changes"}
      />
    </>
  );
}
