import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2Icon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  useAgentRoleProfile,
  useUpdateAgentRoleProfile,
  useUpdateRolePrompt,
} from "@/hooks/useAgentRoleProfile";

const SAVE_DEBOUNCE_MS = 1200;
type SaveStatus = "idle" | "pending" | "saving" | "saved" | "error";

export function RoleDefaultsForm({ roleId }: { roleId: string }) {
  const { data: profile, isLoading, error } = useAgentRoleProfile(roleId);
  const updateProfile = useUpdateAgentRoleProfile(roleId);
  const updatePrompt = useUpdateRolePrompt(roleId);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<SaveStatus>("idle");
  const nameBaseline = useRef("");
  const promptBaseline = useRef("");
  const nameTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const promptTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!profile) return;
    const nextName = profile.title ?? roleId;
    const nextPrompt = profile.prompt ?? "";
    nameBaseline.current = nextName;
    promptBaseline.current = nextPrompt;
    setName(nextName);
    setPrompt(nextPrompt);
    setStatus("idle");
  }, [profile, roleId]);

  useEffect(
    () => () => {
      if (nameTimer.current) clearTimeout(nameTimer.current);
      if (promptTimer.current) clearTimeout(promptTimer.current);
    },
    [],
  );

  const saveName = useCallback(
    (next: string) => {
      if (nameTimer.current) clearTimeout(nameTimer.current);
      if (!next.trim() || next === nameBaseline.current) return;
      setStatus("pending");
      nameTimer.current = setTimeout(() => {
        setStatus("saving");
        updateProfile.mutate(
          { name: next.trim() },
          {
            onSuccess: (saved) => {
              nameBaseline.current = saved.title ?? next.trim();
              setName(nameBaseline.current);
              setStatus("saved");
            },
            onError: () => setStatus("error"),
          },
        );
      }, SAVE_DEBOUNCE_MS);
    },
    [updateProfile],
  );

  const savePrompt = useCallback(
    (next: string) => {
      if (promptTimer.current) clearTimeout(promptTimer.current);
      if (next === promptBaseline.current) return;
      setStatus("pending");
      promptTimer.current = setTimeout(() => {
        setStatus("saving");
        updatePrompt.mutate(next, {
          onSuccess: (saved) => {
            promptBaseline.current = saved.prompt ?? "";
            setPrompt(promptBaseline.current);
            setStatus("saved");
          },
          onError: () => setStatus("error"),
        });
      }, SAVE_DEBOUNCE_MS);
    },
    [updatePrompt],
  );

  if (error) return <p className="text-sm text-destructive">Failed to load role manual.</p>;
  if (isLoading || !profile) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" aria-hidden />
        Loading manual…
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid={`glossary-role-defaults-${roleId}`}>
      <label className="block space-y-1.5 text-xs text-muted-foreground">
        Display name
        <Input
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            saveName(event.target.value);
          }}
        />
      </label>
      <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        Execution target: OmniHarness. Model selection belongs to the PuppyGarden chat sidebar.
      </div>
      <label className="block space-y-1.5 text-xs text-muted-foreground">
        Manual
        <Textarea
          value={prompt}
          rows={10}
          className="resize-y font-mono text-xs"
          onChange={(event) => {
            setPrompt(event.target.value);
            savePrompt(event.target.value);
          }}
        />
      </label>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {status === "pending" && "Unsaved changes…"}
        {status === "saving" && "Saving…"}
        {status === "saved" && "Saved"}
        {status === "error" && "Save failed"}
      </p>
    </div>
  );
}
