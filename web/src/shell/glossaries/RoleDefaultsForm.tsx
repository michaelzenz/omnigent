import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  agentRoleProfileQueryKey,
  useAgentRoleProfile,
  useUpdateAgentRoleProfile,
} from "@/hooks/useAgentRoleProfile";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { useHosts, type Host } from "@/hooks/useHosts";
import { useRecentWorkspaces } from "@/hooks/useRecentWorkspaces";
import type { SecretaryProfile } from "@/lib/agentTasksApi";
import { resetAgentRoleSession } from "@/lib/agentTasksApi";
import { WorkspacePathField } from "@/shell/WorkspacePathField";
import { RoleHarnessPicker } from "./RoleHarnessPicker";

export const ROLE_PROFILE_SAVE_DEBOUNCE_MS = 2000;

type SaveStatus = "idle" | "pending" | "saving" | "saved" | "error";

interface DraftProfile {
  agent_profile_id: string;
  host_id: string;
  workspace: string;
  harness: string;
  model: string;
  conversation_id: string | null;
}

function profileToDraft(profile: SecretaryProfile): DraftProfile {
  return {
    agent_profile_id: profile.agent_profile_id ?? "",
    host_id: profile.host_id ?? "",
    workspace: profile.workspace ?? "",
    harness: profile.harness ?? "",
    model: profile.model ?? "",
    conversation_id: profile.conversation_id,
  };
}

function draftsEqual(a: DraftProfile, b: DraftProfile): boolean {
  return (
    a.agent_profile_id === b.agent_profile_id &&
    a.host_id === b.host_id &&
    a.workspace === b.workspace &&
    a.harness === b.harness &&
    a.model === b.model
  );
}

function HostStatusDot({ host }: { host: Host | undefined }) {
  if (!host) return null;
  const online = host.status === "online";
  return (
    <span
      className={`inline-block size-1.5 rounded-full ${online ? "bg-green-500" : "bg-muted-foreground"}`}
      aria-hidden
    />
  );
}

interface RoleDefaultsFormProps {
  roleId: string;
}

export function RoleDefaultsForm({ roleId }: RoleDefaultsFormProps) {
  const { data: profile, isLoading, error } = useAgentRoleProfile(roleId);
  const { data: hosts = [] } = useHosts();
  const { data: agents = [] } = useAvailableAgents();
  const updateProfile = useUpdateAgentRoleProfile(roleId);
  const [draft, setDraft] = useState<DraftProfile | null>(null);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const serverDraftRef = useRef<DraftProfile | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionBaselineRef = useRef<{ host_id: string; workspace: string } | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!profile) return;
    const next = profileToDraft(profile);
    serverDraftRef.current = next;
    setDraft(next);
    setSaveStatus("idle");
    setSaveError(null);
    if (profile.conversation_id) {
      sessionBaselineRef.current = {
        host_id: profile.host_id ?? "",
        workspace: profile.workspace ?? "",
      };
    } else {
      sessionBaselineRef.current = null;
    }
  }, [profile]);

  const selectedHost = useMemo(
    () => hosts.find((h) => h.host_id === draft?.host_id) ?? null,
    [hosts, draft?.host_id],
  );

  const recentWorkspaces = useRecentWorkspaces(draft?.host_id || null);

  const scheduleSave = useCallback(
    (nextDraft: DraftProfile) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      const baseline = serverDraftRef.current;
      if (!baseline || draftsEqual(nextDraft, baseline)) {
        setSaveStatus("idle");
        return;
      }
      setSaveStatus("pending");
      debounceRef.current = setTimeout(() => {
        setSaveStatus("saving");
        updateProfile.mutate(
          {
            agent_profile_id: nextDraft.agent_profile_id,
            host_id: nextDraft.host_id || null,
            workspace: nextDraft.workspace || null,
            harness: nextDraft.harness || null,
            model: nextDraft.model || null,
          },
          {
            onSuccess: (saved) => {
              const synced = profileToDraft(saved);
              serverDraftRef.current = synced;
              setDraft(synced);
              setSaveStatus("saved");
              setSaveError(null);
            },
            onError: (err) => {
              setSaveStatus("error");
              setSaveError(err instanceof Error ? err.message : "Failed to save");
            },
          },
        );
      }, ROLE_PROFILE_SAVE_DEBOUNCE_MS);
    },
    [updateProfile],
  );

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const patchDraft = useCallback(
    (patch: Partial<DraftProfile>) => {
      setDraft((current) => {
        if (!current) return current;
        const next = { ...current, ...patch };
        scheduleSave(next);
        return next;
      });
    },
    [scheduleSave],
  );

  const showSessionWarning =
    draft?.conversation_id != null &&
    sessionBaselineRef.current != null &&
    (draft.host_id !== sessionBaselineRef.current.host_id ||
      draft.workspace !== sessionBaselineRef.current.workspace);

  async function handleResetSession() {
    setResetting(true);
    try {
      await resetAgentRoleSession(roleId);
      sessionBaselineRef.current = null;
      await queryClient.invalidateQueries({ queryKey: agentRoleProfileQueryKey(roleId) });
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to reset session");
      setSaveStatus("error");
    } finally {
      setResetting(false);
    }
  }

  if (isLoading || !draft) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" aria-hidden />
        Loading defaults…
      </div>
    );
  }

  if (error) {
    return <p className="text-sm text-destructive">Failed to load role defaults.</p>;
  }

  return (
    <div className="space-y-4" data-testid={`glossary-role-defaults-${roleId}`}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <span className="text-xs text-muted-foreground">Host</span>
          <Select
            value={draft.host_id || undefined}
            onValueChange={(host_id) => patchDraft({ host_id })}
          >
            <SelectTrigger className="w-full" data-testid={`glossary-role-host-${roleId}`}>
              <SelectValue placeholder="Select host" />
            </SelectTrigger>
            <SelectContent>
              {hosts.map((host) => (
                <SelectItem key={host.host_id} value={host.host_id}>
                  <span className="flex items-center gap-2">
                    <HostStatusDot host={host} />
                    <span>{host.name}</span>
                    <span className="text-muted-foreground">({host.status})</span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5 sm:col-span-2">
          <span className="text-xs text-muted-foreground">Workspace</span>
          <WorkspacePathField
            hostId={draft.host_id || null}
            value={draft.workspace}
            onChange={(workspace) => patchDraft({ workspace })}
            onBrowse={() => {}}
            recent={recentWorkspaces.recent}
          />
        </div>

        <div className="space-y-1.5 sm:col-span-2">
          <span className="text-xs text-muted-foreground">Harness</span>
          <RoleHarnessPicker
            host={selectedHost}
            agents={agents}
            harness={draft.harness}
            model={draft.model}
            testId={`glossary-role-harness-${roleId}`}
            onChange={({ harness, model }) => patchDraft({ harness, model })}
          />
        </div>
      </div>

      {showSessionWarning ? (
        <div
          className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
          data-testid={`glossary-role-session-warning-${roleId}`}
        >
          Saved for next bootstrap. The current session still runs on the previous host or
          workspace. Reset the role session to apply.
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-2 h-7"
            disabled={resetting}
            onClick={() => void handleResetSession()}
          >
            {resetting ? "Resetting…" : "Reset session"}
          </Button>
        </div>
      ) : null}

      <p className="text-xs text-muted-foreground" aria-live="polite">
        {saveStatus === "pending" && "Unsaved changes…"}
        {saveStatus === "saving" && "Saving…"}
        {saveStatus === "saved" && "Saved"}
        {saveStatus === "error" && (saveError ?? "Save failed")}
      </p>
    </div>
  );
}
