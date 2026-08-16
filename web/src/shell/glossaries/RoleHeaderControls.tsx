import { useCallback, useEffect, useRef, useState } from "react";
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
  useImportRoleAgent,
  useUpdateAgentRoleProfile,
} from "@/hooks/useAgentRoleProfile";
import type { RoleCandidateAgent } from "@/lib/agentTasksApi";

/**
 * Compact agent-source picker + Import button, sized for the card header
 * (right of the title). Selecting a packaged source and clicking Import
 * copies that source's spec into the role's bound backing profile.
 */
export function RoleAgentPicker({ roleId }: { roleId: string }) {
  const { data: profile } = useAgentRoleProfile(roleId);
  const importAgent = useImportRoleAgent(roleId);
  const candidates: RoleCandidateAgent[] = profile?.candidate_agents ?? [];
  const [sourceId, setSourceId] = useState<string>("");
  const selected = candidates.find((c) => c.id === sourceId) ?? null;

  useEffect(() => {
    if (!sourceId && candidates.length > 0) {
      setSourceId(candidates[0].id);
    }
  }, [candidates, sourceId]);

  if (candidates.length === 0) return null;
  const canImport = selected?.packaged === true && !importAgent.isPending;

  return (
    <div className="flex items-center gap-1.5" data-testid={`glossary-role-agent-${roleId}`}>
      <Select value={sourceId || undefined} onValueChange={setSourceId}>
        <SelectTrigger className="h-7 w-40 text-xs" aria-label="Agent profile source">
          <SelectValue placeholder="Profile" />
        </SelectTrigger>
        <SelectContent>
          {candidates.map((c) => (
            <SelectItem key={c.id} value={c.id}>
              {c.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 px-2 text-xs"
        disabled={!canImport}
        onClick={() => selected && importAgent.mutate(selected.id)}
        data-testid={`glossary-role-agent-import-${roleId}`}
        title="Copy this profile's spec into this role's bound profile"
      >
        {importAgent.isPending ? "Importing…" : "Import"}
      </Button>
      {importAgent.error ? (
        <span className="text-xs text-destructive">
          {importAgent.error instanceof Error ? importAgent.error.message : "Import failed"}
        </span>
      ) : null}
    </div>
  );
}

const DESCRIPTION_SAVE_DEBOUNCE_MS = 1200;

/**
 * Editable role description rendered under the card title. Debounced save
 * of the description field only (the rest of the profile is saved by the
 * defaults form).
 */
export function RoleDescriptionField({ roleId }: { roleId: string }) {
  const { data: profile } = useAgentRoleProfile(roleId);
  const updateProfile = useUpdateAgentRoleProfile(roleId);
  const [value, setValue] = useState("");
  const [status, setStatus] = useState<"idle" | "pending" | "saving" | "saved" | "error">("idle");
  const baselineRef = useRef<string>("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const next = profile?.description ?? "";
    baselineRef.current = next;
    setValue(next);
    setStatus("idle");
  }, [profile?.description]);

  const schedule = useCallback(
    (next: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (next === baselineRef.current) {
        setStatus("idle");
        return;
      }
      setStatus("pending");
      debounceRef.current = setTimeout(() => {
        setStatus("saving");
        updateProfile.mutate(
          { description: next || null },
          {
            onSuccess: (saved) => {
              baselineRef.current = saved.description ?? "";
              setValue(saved.description ?? "");
              setStatus("saved");
            },
            onError: () => setStatus("error"),
          },
        );
      }, DESCRIPTION_SAVE_DEBOUNCE_MS);
    },
    [updateProfile],
  );

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  return (
    <div className="mt-2" data-testid={`glossary-role-description-${roleId}`}>
      <Textarea
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          schedule(e.target.value);
        }}
        placeholder="What this role specializes in (shown to the manager when picking a worker lane)"
        rows={2}
        className="w-full resize-y text-sm"
      />
      <p className="mt-1 text-xs text-muted-foreground" aria-live="polite">
        {status === "pending" && "Unsaved…"}
        {status === "saving" && "Saving…"}
        {status === "saved" && "Saved"}
        {status === "error" && "Save failed"}
      </p>
    </div>
  );
}

// Re-export for the query key so callers can invalidate after a reset.
export { agentRoleProfileQueryKey };
