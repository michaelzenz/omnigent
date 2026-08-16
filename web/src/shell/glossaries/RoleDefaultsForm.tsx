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
import { Textarea } from "@/components/ui/textarea";
import {
  agentRoleProfileQueryKey,
  useAgentRoleProfile,
  useUpdateAgentRoleProfile,
  useUpdateRolePrompt,
} from "@/hooks/useAgentRoleProfile";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { useHosts, type Host } from "@/hooks/useHosts";
import { useRecentWorkspaces } from "@/hooks/useRecentWorkspaces";
import type { SecretaryProfile } from "@/lib/agentTasksApi";
import { resetAgentRoleSession } from "@/lib/agentTasksApi";
import { WorkspacePathField } from "@/shell/WorkspacePathField";
import { RoleHarnessPicker } from "./RoleHarnessPicker";
import { SDK_HARNESS, SDK_MODEL_OPTIONS } from "./roleProfileOptions";

export const ROLE_PROFILE_SAVE_DEBOUNCE_MS = 2000;
const PROMPT_SAVE_DEBOUNCE_MS = 1500;

type SaveStatus = "idle" | "pending" | "saving" | "saved" | "error";

interface DraftProfile {
  host_id: string;
  workspace: string;
  harness: string;
  model: string;
  conversation_id: string | null;
}

function profileToDraft(profile: SecretaryProfile): DraftProfile {
  return {
    host_id: profile.host_id ?? "",
    workspace: profile.workspace ?? "",
    harness: profile.harness ?? "",
    model: profile.model ?? "",
    conversation_id: profile.conversation_id,
  };
}

function draftsEqual(a: DraftProfile, b: DraftProfile): boolean {
  return (
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
  const updatePrompt = useUpdateRolePrompt(roleId);
  const [draft, setDraft] = useState<DraftProfile | null>(null);
  const [promptDraft, setPromptDraft] = useState<string>("");
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [promptStatus, setPromptStatus] = useState<SaveStatus>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const serverDraftRef = useRef<DraftProfile | null>(null);
  const promptBaselineRef = useRef<string>("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const promptDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionBaselineRef = useRef<{ host_id: string; workspace: string } | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!profile) return;
    const next = profileToDraft(profile);
    serverDraftRef.current = next;
    setDraft(next);
    setSaveStatus("idle");
    setSaveError(null);
    const prompt = profile.prompt ?? "";
    promptBaselineRef.current = prompt;
    setPromptDraft(prompt);
    setPromptStatus("idle");
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

  const schedulePromptSave = useCallback(
    (nextPrompt: string) => {
      if (promptDebounceRef.current) clearTimeout(promptDebounceRef.current);
      if (nextPrompt === promptBaselineRef.current) {
        setPromptStatus("idle");
        return;
      }
      setPromptStatus("pending");
      promptDebounceRef.current = setTimeout(() => {
        setPromptStatus("saving");
        updatePrompt.mutate(nextPrompt, {
          onSuccess: (saved) => {
            promptBaselineRef.current = saved.prompt ?? "";
            setPromptDraft(saved.prompt ?? "");
            setPromptStatus("saved");
          },
          onError: () => setPromptStatus("error"),
        });
      }, PROMPT_SAVE_DEBOUNCE_MS);
    },
    [updatePrompt],
  );

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (promptDebounceRef.current) clearTimeout(promptDebounceRef.current);
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

        {draft.harness === SDK_HARNESS ? (
          <div className="space-y-1.5 sm:col-span-2" data-testid={`glossary-role-model-${roleId}`}>
            <span className="text-xs text-muted-foreground">Model</span>
            <Select
              value={draft.model || undefined}
              onValueChange={(model) => patchDraft({ model })}
            >
              <SelectTrigger
                className="w-full"
                data-testid={`glossary-role-model-select-${roleId}`}
              >
                <SelectValue placeholder="Select model" />
              </SelectTrigger>
              <SelectContent>
                {SDK_MODEL_OPTIONS.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.displayName}
                  </SelectItem>
                ))}
                {draft.model && !SDK_MODEL_OPTIONS.some((option) => option.id === draft.model) ? (
                  <SelectItem value={draft.model}>{draft.model}</SelectItem>
                ) : null}
              </SelectContent>
            </Select>
          </div>
        ) : null}
      </div>

      <div className="space-y-1.5" data-testid={`glossary-role-prompt-${roleId}`}>
        <span className="text-xs text-muted-foreground">Prompt</span>
        <Textarea
          value={promptDraft}
          onChange={(e) => {
            setPromptDraft(e.target.value);
            schedulePromptSave(e.target.value);
          }}
          placeholder="System prompt for this role's agent"
          rows={8}
          className="w-full resize-y font-mono text-xs"
          data-testid={`glossary-role-prompt-input-${roleId}`}
        />
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {promptStatus === "pending" && "Unsaved changes…"}
          {promptStatus === "saving" && "Saving…"}
          {promptStatus === "saved" && "Saved"}
          {promptStatus === "error" && "Save failed"}
        </p>
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
