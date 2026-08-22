import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2Icon } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  useAgentRoleProfile,
  useUpdateAgentRoleProfile,
  useUpdateRolePrompt,
} from "@/hooks/useAgentRoleProfile";
import { useOmniHarnessModelOptions } from "@/hooks/useModelSettings";

const MODEL_DEFAULT_VALUE = "__default__";

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

  const { data: modelOptions } = useOmniHarnessModelOptions();

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

  const modelValue = profile?.model ?? MODEL_DEFAULT_VALUE;

  const modelSelectOptions = useMemo(
    () => [
      { value: MODEL_DEFAULT_VALUE, label: "Default" },
      ...(modelOptions ?? []).map((opt) => ({ value: opt.id, label: opt.displayName })),
    ],
    [modelOptions],
  );

  const saveModel = useCallback(
    (value: string) => {
      const next = value === MODEL_DEFAULT_VALUE ? null : value;
      if (next === (profile?.model ?? null)) return;
      setStatus("saving");
      updateProfile.mutate(
        { model: next },
        {
          onSuccess: () => setStatus("saved"),
          onError: () => setStatus("error"),
        },
      );
    },
    [updateProfile, profile?.model],
  );

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
      <div className="flex items-end gap-3">
        <label className="block flex-1 space-y-1.5 text-xs text-muted-foreground">
          Display name
          <Input
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              saveName(event.target.value);
            }}
          />
        </label>
        <label className="block w-40 shrink-0 space-y-1.5 text-xs text-muted-foreground">
          Model
          <Select value={modelValue} onValueChange={saveModel}>
            <SelectTrigger className="w-full" data-testid={`glossary-role-model-${roleId}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent position="popper" align="start">
              {modelSelectOptions.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
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
