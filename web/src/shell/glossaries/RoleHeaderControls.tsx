import { useCallback, useEffect, useRef, useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import {
  agentRoleProfileQueryKey,
  useAgentRoleProfile,
  useUpdateAgentRoleProfile,
} from "@/hooks/useAgentRoleProfile";

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

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  return (
    <div className="mt-2" data-testid={`glossary-role-description-${roleId}`}>
      <Textarea
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          schedule(e.target.value);
        }}
        placeholder="Describe when this manager role should be selected"
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
