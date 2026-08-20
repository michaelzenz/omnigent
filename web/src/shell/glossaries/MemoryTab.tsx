import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  PlusIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  useCreateMemoryCategory,
  useDeleteMemoryCategory,
  useMemory,
  useMemoryFileVariants,
  useReorderMemoryCategories,
  useSyncMemoryFileVariant,
  useUpdateMemoryCategory,
  useUpdateMemoryFileVariant,
  useUpdateMemorySettings,
  type MemoryCategory,
  type MemoryFileVariant,
  type MemoryProvider,
} from "@/hooks/useMemory";
import { cn } from "@/lib/utils";

type SaveStatus = "Unsaved" | "Saving" | "Saved" | "Save failed";
type InclusionStatus = "included" | "partial" | "truncated";

const AUTOSAVE_DELAY_MS = 1_200;
const CATEGORY_COLORS = [
  "bg-blue-500",
  "bg-violet-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-rose-500",
  "bg-cyan-500",
  "bg-orange-500",
  "bg-fuchsia-500",
] as const;

function inclusionStatuses(categories: MemoryCategory[], maxTokens: number) {
  let used = 0;
  return new Map(
    categories.map((category) => {
      const remaining = Math.max(0, maxTokens - used);
      const status: InclusionStatus =
        category.token_count <= remaining ? "included" : remaining > 0 ? "partial" : "truncated";
      used += category.token_count;
      return [category.id, status];
    }),
  );
}

function categoryColorClass(categoryId: string) {
  let hash = 0;
  for (const character of categoryId) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  }
  return CATEGORY_COLORS[hash % CATEGORY_COLORS.length];
}

function formatUsagePercent(value: number) {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}% used`;
}

export function MemoryTab() {
  const memory = useMemory();
  const createCategory = useCreateMemoryCategory();
  const updateCategory = useUpdateMemoryCategory();
  const deleteCategory = useDeleteMemoryCategory();
  const reorderCategories = useReorderMemoryCategories();
  const updateSettings = useUpdateMemorySettings();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saveStatuses, setSaveStatuses] = useState<Record<string, SaveStatus>>({});
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [nameDraft, setNameDraft] = useState("");
  const [limitDraft, setLimitDraft] = useState("");
  const [operationError, setOperationError] = useState<string | null>(null);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const revisions = useRef(new Map<string, number>());

  const categories = useMemo(() => memory.data?.categories ?? [], [memory.data?.categories]);
  const memoryMaxTokens = memory.data?.max_tokens;
  const selected = categories.find((category) => category.id === selectedId) ?? null;
  const statuses = useMemo(
    () => inclusionStatuses(categories, memory.data?.max_tokens ?? 0),
    [categories, memory.data?.max_tokens],
  );

  useEffect(() => {
    if (!categories.length) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !categories.some((category) => category.id === selectedId)) {
      setSelectedId(categories[0].id);
    }
  }, [categories, selectedId]);

  useEffect(() => {
    setNameDraft(selected?.name ?? "");
  }, [selected?.id, selected?.name]);

  useEffect(() => {
    if (memoryMaxTokens !== undefined) setLimitDraft(String(memoryMaxTokens));
  }, [memoryMaxTokens]);

  useEffect(
    () => () => {
      for (const timer of timers.current.values()) clearTimeout(timer);
    },
    [],
  );

  function scheduleSave(categoryId: string, content: string) {
    const previousTimer = timers.current.get(categoryId);
    if (previousTimer) clearTimeout(previousTimer);
    const revision = (revisions.current.get(categoryId) ?? 0) + 1;
    revisions.current.set(categoryId, revision);
    setDrafts((current) => ({ ...current, [categoryId]: content }));
    setSaveStatuses((current) => ({ ...current, [categoryId]: "Unsaved" }));
    timers.current.set(
      categoryId,
      setTimeout(() => {
        timers.current.delete(categoryId);
        setSaveStatuses((current) => ({ ...current, [categoryId]: "Saving" }));
        void updateCategory
          .mutateAsync({ id: categoryId, content })
          .then(() => {
            if (revisions.current.get(categoryId) !== revision) return;
            setDrafts((current) =>
              Object.fromEntries(Object.entries(current).filter(([id]) => id !== categoryId)),
            );
            setSaveStatuses((current) => ({ ...current, [categoryId]: "Saved" }));
          })
          .catch(() => {
            if (revisions.current.get(categoryId) !== revision) return;
            setSaveStatuses((current) => ({ ...current, [categoryId]: "Save failed" }));
          });
      }, AUTOSAVE_DELAY_MS),
    );
  }

  async function handleCreate() {
    const name = newName.trim();
    if (!name) return;
    setOperationError(null);
    try {
      const existingIds = new Set(categories.map((category) => category.id));
      const response = await createCategory.mutateAsync({ name });
      const created = response.categories.find((category) => !existingIds.has(category.id));
      setSelectedId(created?.id ?? response.categories.at(-1)?.id ?? null);
      setNewName("");
      setAdding(false);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Could not create category.");
    }
  }

  async function handleRename() {
    if (!selected) return;
    const name = nameDraft.trim();
    if (!name || name === selected.name) {
      setNameDraft(selected.name);
      return;
    }
    setOperationError(null);
    try {
      await updateCategory.mutateAsync({ id: selected.id, name });
    } catch (error) {
      setNameDraft(selected.name);
      setOperationError(error instanceof Error ? error.message : "Could not rename category.");
    }
  }

  async function handleLimitUpdate() {
    if (!memory.data) return;
    const maxTokens = Number(limitDraft);
    if (
      !Number.isInteger(maxTokens) ||
      maxTokens < 1 ||
      maxTokens > 1_000_000 ||
      maxTokens === memory.data.max_tokens
    ) {
      setLimitDraft(String(memory.data.max_tokens));
      return;
    }
    setOperationError(null);
    try {
      await updateSettings.mutateAsync(maxTokens);
    } catch (error) {
      setLimitDraft(String(memory.data.max_tokens));
      setOperationError(error instanceof Error ? error.message : "Could not update memory limit.");
    }
  }

  async function handleProviderUpdate(provider: MemoryProvider) {
    if (provider === memory.data?.provider) return;
    setOperationError(null);
    try {
      await updateSettings.mutateAsync({ provider });
    } catch (error) {
      setOperationError(
        error instanceof Error ? error.message : "Could not update global memory provider.",
      );
    }
  }

  async function handleDelete(category: MemoryCategory) {
    const content = drafts[category.id] ?? category.content;
    if (content && !window.confirm(`Delete non-empty category "${category.name}"?`)) return;
    setOperationError(null);
    const index = categories.findIndex((item) => item.id === category.id);
    const timer = timers.current.get(category.id);
    if (timer) clearTimeout(timer);
    timers.current.delete(category.id);
    revisions.current.delete(category.id);
    try {
      const response = await deleteCategory.mutateAsync(category.id);
      if (selectedId === category.id) {
        setSelectedId(response.categories[index]?.id ?? response.categories[index - 1]?.id ?? null);
      }
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Could not delete category.");
    }
  }

  async function moveCategory(index: number, offset: -1 | 1) {
    const target = index + offset;
    if (target < 0 || target >= categories.length) return;
    const orderedIds = categories.map((category) => category.id);
    [orderedIds[index], orderedIds[target]] = [orderedIds[target], orderedIds[index]];
    setOperationError(null);
    try {
      await reorderCategories.mutateAsync(orderedIds);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Could not reorder categories.");
    }
  }

  if (memory.isLoading) {
    return (
      <div className="grid min-h-80 place-items-center text-sm text-muted-foreground">
        Loading memory…
      </div>
    );
  }

  if (memory.isError || !memory.data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load memory</AlertTitle>
        <AlertDescription>
          {memory.error instanceof Error ? memory.error.message : "Please try again."}
        </AlertDescription>
      </Alert>
    );
  }

  const currentContent = selected ? (drafts[selected.id] ?? selected.content) : "";
  const currentSaveStatus = selected ? (saveStatuses[selected.id] ?? "Saved") : "Saved";
  let composedTokens = 0;
  const composition = categories.map((category) => {
    const includedTokens = Math.min(
      category.token_count,
      Math.max(0, memory.data.max_tokens - composedTokens),
    );
    composedTokens += includedTokens;
    return { category, includedTokens };
  });

  return (
    <div className="space-y-4" data-testid="glossaries-memory-tab">
      {memory.data.provider === "omniharness" && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-4 text-sm">
            <span className="font-medium">{formatUsagePercent(memory.data.usage_percent)}</span>
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">
                {memory.data.used_tokens.toLocaleString()} tokens used
              </span>
              <label className="flex items-center gap-2">
                <span className="text-muted-foreground">Memory limit</span>
                <Input
                  aria-label="Memory token limit"
                  type="number"
                  min={1}
                  max={1_000_000}
                  value={limitDraft}
                  className="h-7 w-28 text-right tabular-nums"
                  disabled={updateSettings.isPending}
                  onChange={(event) => setLimitDraft(event.target.value)}
                  onBlur={() => void handleLimitUpdate()}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                    if (event.key === "Escape") {
                      setLimitDraft(String(memory.data.max_tokens));
                      event.currentTarget.blur();
                    }
                  }}
                />
              </label>
            </div>
          </div>
          <div
            role="progressbar"
            aria-label="Memory token usage"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.min(100, Math.max(0, memory.data.usage_percent))}
            aria-valuetext={formatUsagePercent(memory.data.usage_percent)}
            className="flex h-2 overflow-hidden rounded-full bg-muted"
          >
            {composition.map(({ category, includedTokens }) =>
              includedTokens > 0 ? (
                <span
                  key={category.id}
                  title={`${category.name}: ${includedTokens.toLocaleString()} injected tokens`}
                  className={cn("h-full shrink-0", categoryColorClass(category.id))}
                  style={{
                    width: `${(includedTokens / memory.data.max_tokens) * 100}%`,
                  }}
                />
              ) : null,
            )}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1">
            {categories.map((category) => (
              <span
                key={category.id}
                className="flex items-center gap-1.5 text-xs text-muted-foreground"
              >
                <span
                  aria-hidden="true"
                  className={cn("size-2 rounded-full", categoryColorClass(category.id))}
                />
                {category.name}
                <span className="tabular-nums">{category.token_count.toLocaleString()}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <Alert>
        <AlertTitle>OmniHarness only</AlertTitle>
        <AlertDescription>
          This memory is always injected into sessions using OmniHarness. Other harnesses are
          unaffected.
        </AlertDescription>
      </Alert>

      <div className="grid gap-3 rounded-lg border border-border bg-card p-3 md:grid-cols-[minmax(14rem,20rem)_minmax(0,1fr)] md:items-end">
        <label className="space-y-1.5">
          <span className="text-sm font-medium">Global Memory Provider</span>
          <Select
            value={memory.data.provider}
            disabled={updateSettings.isPending}
            onValueChange={(value) => void handleProviderUpdate(value as MemoryProvider)}
          >
            <SelectTrigger aria-label="Global Memory Provider">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="omniharness">OmniHarness</SelectItem>
              <SelectItem value="claude">CLAUDE.md</SelectItem>
              <SelectItem value="agents">AGENTS.md</SelectItem>
            </SelectContent>
          </Select>
        </label>
        {memory.data.provider !== "omniharness" && (
          <p className="rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground">
            OmniHarness will also read{" "}
            <span className="font-medium text-foreground">
              {memory.data.provider === "claude" ? "CLAUDE.md" : "AGENTS.md"}
            </span>{" "}
            files from the project root to the session working directory.
          </p>
        )}
      </div>

      {memory.data.provider === "omniharness" && memory.data.over_limit && (
        <Alert className="border-amber-500/50 bg-amber-500/5">
          <AlertTriangleIcon className="text-amber-600" />
          <AlertTitle>
            Memory is over the {memory.data.max_tokens.toLocaleString()}-token limit
          </AlertTitle>
          <AlertDescription>
            Overflow is truncated when memory is injected. Categories are included from top to
            bottom; the rail shows which are included, partial, or truncated.
          </AlertDescription>
        </Alert>
      )}

      {operationError && (
        <Alert variant="destructive">
          <AlertTitle>Memory update failed</AlertTitle>
          <AlertDescription>{operationError}</AlertDescription>
        </Alert>
      )}

      {memory.data.provider === "omniharness" ? (
        <div className="grid min-h-[34rem] grid-cols-1 overflow-hidden rounded-lg border border-border bg-background lg:grid-cols-[18rem_minmax(0,1fr)]">
          <section className="flex min-h-0 flex-col border-b border-border lg:border-r lg:border-b-0">
            <header className="flex items-center justify-between border-b border-border p-3">
              <div>
                <h2 className="text-sm font-medium">Categories</h2>
                <p className="text-xs text-muted-foreground">Included from top to bottom</p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setAdding(true)} disabled={adding}>
                <PlusIcon /> Add
              </Button>
            </header>

            {adding && (
              <form
                className="flex gap-2 border-b border-border p-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  void handleCreate();
                }}
              >
                <Input
                  autoFocus
                  aria-label="New category name"
                  value={newName}
                  onChange={(event) => setNewName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      setAdding(false);
                      setNewName("");
                    }
                  }}
                />
                <Button
                  type="submit"
                  size="sm"
                  disabled={!newName.trim() || createCategory.isPending}
                >
                  Add
                </Button>
              </form>
            )}

            <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
              {categories.map((category, index) => {
                const active = category.id === selected?.id;
                const inclusion = statuses.get(category.id) ?? "included";
                return (
                  <div
                    key={category.id}
                    className={cn(
                      "group flex items-center gap-1 rounded-md px-1 py-1",
                      active && "bg-muted",
                    )}
                  >
                    <button
                      type="button"
                      aria-label={`Select ${category.name}`}
                      className="flex min-w-0 flex-1 items-center gap-2 px-1.5 py-1.5 text-left"
                      onClick={() => setSelectedId(category.id)}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "size-2 shrink-0 rounded-full",
                          categoryColorClass(category.id),
                        )}
                      />
                      <span className="sr-only">{inclusion}: </span>
                      <span
                        className={cn("min-w-0 flex-1 truncate text-sm", active && "font-medium")}
                      >
                        {category.name}
                      </span>
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {category.token_count.toLocaleString()}
                      </span>
                      {inclusion !== "included" && (
                        <span
                          className={cn(
                            "text-[10px] uppercase tracking-wide",
                            inclusion === "partial"
                              ? "text-amber-600 dark:text-amber-400"
                              : "text-muted-foreground",
                          )}
                        >
                          {inclusion}
                        </span>
                      )}
                    </button>
                    <div className="flex shrink-0">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Move ${category.name} up`}
                        disabled={index === 0 || reorderCategories.isPending}
                        onClick={() => void moveCategory(index, -1)}
                      >
                        <ArrowUpIcon />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Move ${category.name} down`}
                        disabled={index === categories.length - 1 || reorderCategories.isPending}
                        onClick={() => void moveCategory(index, 1)}
                      >
                        <ArrowDownIcon />
                      </Button>
                    </div>
                  </div>
                );
              })}
              {!categories.length && (
                <p className="p-5 text-center text-sm text-muted-foreground">
                  Add a category to start building memory.
                </p>
              )}
            </div>
          </section>

          <section className="flex min-h-0 min-w-0 flex-col">
            {!selected ? (
              <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
                Select or add a category.
              </div>
            ) : (
              <>
                <header className="flex items-center gap-3 border-b border-border p-3">
                  <Input
                    aria-label="Category name"
                    value={nameDraft}
                    maxLength={100}
                    className="max-w-sm font-medium"
                    onChange={(event) => setNameDraft(event.target.value)}
                    onBlur={() => void handleRename()}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") event.currentTarget.blur();
                      if (event.key === "Escape") {
                        setNameDraft(selected.name);
                        event.currentTarget.blur();
                      }
                    }}
                  />
                  <Badge
                    aria-live="polite"
                    variant={currentSaveStatus === "Save failed" ? "destructive" : "outline"}
                    className="ml-auto"
                  >
                    {currentSaveStatus}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Delete ${selected.name}`}
                    disabled={deleteCategory.isPending}
                    onClick={() => void handleDelete(selected)}
                  >
                    <Trash2Icon />
                  </Button>
                </header>
                <div className="flex min-h-0 flex-1 flex-col gap-2 p-3">
                  <Textarea
                    aria-label={`${selected.name} memory`}
                    value={currentContent}
                    onChange={(event) => scheduleSave(selected.id, event.target.value)}
                    className="min-h-[26rem] flex-1 resize-none font-mono text-sm"
                    spellCheck={false}
                  />
                  <div className="flex justify-between gap-3 text-xs text-muted-foreground">
                    <span>Changes save automatically</span>
                    <span>{selected.token_count.toLocaleString()} tokens</span>
                  </div>
                </div>
              </>
            )}
          </section>
        </div>
      ) : (
        <FileMemoryBoard
          provider={memory.data.provider}
          maxTokens={memory.data.max_tokens}
          onLimitUpdate={(value) => updateSettings.mutateAsync(value)}
        />
      )}
    </div>
  );
}

function FileMemoryBoard({
  provider,
  maxTokens,
  onLimitUpdate,
}: {
  provider: Exclude<MemoryProvider, "omniharness">;
  maxTokens: number;
  onLimitUpdate: (maxTokens: number) => Promise<unknown>;
}) {
  const files = useMemoryFileVariants(provider);
  const updateVariant = useUpdateMemoryFileVariant();
  const syncVariant = useSyncMemoryFileVariant();
  const [selectedHash, setSelectedHash] = useState<string | null>(null);
  const [selectedHostId, setSelectedHostId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("Saved");
  const [limitDraft, setLimitDraft] = useState(String(maxTokens));
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const revision = useRef(0);
  const selectedHashRef = useRef<string | null>(null);
  const saveChain = useRef<Promise<unknown>>(Promise.resolve());
  const variants = files.data?.variants ?? [];
  const selected =
    variants.find((variant) => variant.content_sha256 === selectedHash) ??
    variants.find((variant) => variant.hosts.some((host) => host.host_id === selectedHostId)) ??
    variants[0] ??
    null;
  const filename = provider === "claude" ? "CLAUDE.md" : "AGENTS.md";

  useEffect(() => {
    if (selected && selected.content_sha256 !== selectedHash) {
      selectedHashRef.current = selected.content_sha256;
      setSelectedHash(selected.content_sha256);
    }
    if (selected && !selectedHostId) {
      setSelectedHostId(
        selected.hosts.find((host) => host.online)?.host_id ?? selected.hosts[0]?.host_id ?? null,
      );
    }
  }, [selected, selectedHash, selectedHostId]);

  useEffect(() => {
    setDraft(selected?.content ?? "");
    setSaveStatus("Saved");
  }, [selected?.content_sha256, selected?.content]);

  useEffect(() => setLimitDraft(String(maxTokens)), [maxTokens]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  function scheduleSave(variant: MemoryFileVariant, content: string) {
    if (timer.current) clearTimeout(timer.current);
    const currentRevision = ++revision.current;
    setDraft(content);
    setSaveStatus("Unsaved");
    timer.current = setTimeout(() => {
      timer.current = null;
      setSaveStatus("Saving");
      const targetHostId =
        variant.hosts.find((host) => host.online)?.host_id ?? variant.hosts[0]?.host_id ?? null;
      saveChain.current = saveChain.current
        .catch(() => undefined)
        .then(() =>
          updateVariant.mutateAsync({
            provider,
            contentSha256: selectedHashRef.current ?? variant.content_sha256,
            content,
          }),
        )
        .then((response) => {
          const nextVariant = response.variants.find((item) =>
            item.hosts.some((host) => host.host_id === targetHostId),
          );
          if (nextVariant) {
            selectedHashRef.current = nextVariant.content_sha256;
            setSelectedHash(nextVariant.content_sha256);
          }
          return response;
        })
        .then(() => {
          if (revision.current === currentRevision) setSaveStatus("Saved");
        })
        .catch((cause) => {
          if (revision.current === currentRevision) setSaveStatus("Save failed");
          setError(cause instanceof Error ? cause.message : "Could not save this variant.");
        });
    }, AUTOSAVE_DELAY_MS);
  }

  async function updateLimit() {
    const value = Number(limitDraft);
    if (!Number.isInteger(value) || value < 1 || value > 1_000_000 || value === maxTokens) {
      setLimitDraft(String(maxTokens));
      return;
    }
    try {
      await onLimitUpdate(value);
    } catch (cause) {
      setLimitDraft(String(maxTokens));
      setError(cause instanceof Error ? cause.message : "Could not update memory limit.");
    }
  }

  async function syncToAll() {
    if (!selected) return;
    const targetCount = (files.data?.hosts ?? []).filter(
      (host) => host.content_sha256 !== selected.content_sha256,
    ).length;
    if (
      targetCount > 0 &&
      !window.confirm(`Replace ${filename} on ${targetCount} other host(s) with this variant?`)
    ) {
      return;
    }
    setError(null);
    try {
      await syncVariant.mutateAsync({
        provider,
        sourceSha256: selected.content_sha256,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not sync this variant.");
    }
  }

  if (files.isLoading) {
    return (
      <div className="grid min-h-[34rem] place-items-center rounded-lg border text-sm text-muted-foreground">
        Loading {filename} from hosts…
      </div>
    );
  }

  if (files.isError || !files.data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load {filename}</AlertTitle>
        <AlertDescription>
          {files.error instanceof Error ? files.error.message : "Please try again."}
        </AlertDescription>
      </Alert>
    );
  }

  const usedTokens = selected?.token_count ?? 0;
  const usagePercent = maxTokens ? (usedTokens / maxTokens) * 100 : 0;
  const unknownHosts = files.data.hosts.filter((host) => host.status === "unknown");

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="font-medium">{formatUsagePercent(usagePercent)}</span>
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">
              {usedTokens.toLocaleString()} tokens in selected variant
            </span>
            <label className="flex items-center gap-2">
              <span className="text-muted-foreground">Memory limit</span>
              <Input
                aria-label="Memory token limit"
                type="number"
                min={1}
                max={1_000_000}
                value={limitDraft}
                className="h-7 w-28 text-right tabular-nums"
                onChange={(event) => setLimitDraft(event.target.value)}
                onBlur={() => void updateLimit()}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur();
                  if (event.key === "Escape") {
                    setLimitDraft(String(maxTokens));
                    event.currentTarget.blur();
                  }
                }}
              />
            </label>
          </div>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div
            className={cn("h-full", usagePercent > 100 ? "bg-amber-500" : "bg-blue-500")}
            style={{ width: `${Math.min(100, usagePercent)}%` }}
          />
        </div>
      </div>

      {usagePercent > 100 && (
        <Alert className="border-amber-500/50 bg-amber-500/5">
          <AlertTriangleIcon className="text-amber-600" />
          <AlertTitle>{filename} is over the memory token limit</AlertTitle>
          <AlertDescription>
            The selected file hierarchy is truncated when injected into OmniHarness.
          </AlertDescription>
        </Alert>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Global memory update failed</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid min-h-[34rem] grid-cols-1 overflow-hidden rounded-lg border border-border bg-background lg:grid-cols-[20rem_minmax(0,1fr)]">
        <section className="border-b border-border lg:border-r lg:border-b-0">
          <header className="border-b border-border p-3">
            <h2 className="text-sm font-medium">Host variants</h2>
            <p className="text-xs text-muted-foreground">~/{files.data.rel_home_path}</p>
          </header>
          <div className="space-y-1 p-2">
            {variants.map((variant, index) => (
              <button
                key={variant.content_sha256}
                type="button"
                aria-label={`Select Variant ${index + 1}`}
                className={cn(
                  "w-full rounded-md border px-3 py-2 text-left",
                  selected?.content_sha256 === variant.content_sha256
                    ? "border-primary bg-muted"
                    : "border-transparent hover:bg-muted/60",
                )}
                disabled={saveStatus !== "Saved"}
                onClick={() => {
                  selectedHashRef.current = variant.content_sha256;
                  setSelectedHash(variant.content_sha256);
                  setSelectedHostId(
                    variant.hosts.find((host) => host.online)?.host_id ??
                      variant.hosts[0]?.host_id ??
                      null,
                  );
                }}
              >
                <span className="flex items-center justify-between gap-2 text-sm font-medium">
                  {variant.content_sha256 === "missing" ? "Missing file" : `Variant ${index + 1}`}
                  <Badge variant="outline">{variant.hosts.length} host(s)</Badge>
                </span>
                <span className="mt-1 flex flex-wrap gap-1">
                  {variant.hosts.map((host) => (
                    <Badge
                      key={host.host_id}
                      variant={host.online ? "secondary" : "outline"}
                      className={cn(!host.online && "opacity-60")}
                    >
                      {host.host_name}
                      {!host.online ? " · offline" : ""}
                    </Badge>
                  ))}
                </span>
              </button>
            ))}
            {unknownHosts.length > 0 && (
              <div className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
                <p className="font-medium text-foreground">Missing or unavailable</p>
                {unknownHosts.map((host) => (
                  <p key={host.host_id}>
                    {host.host_name} · {host.online ? host.status : "offline"}
                  </p>
                ))}
              </div>
            )}
          </div>
        </section>

        <section className="flex min-h-0 min-w-0 flex-col">
          <header className="flex flex-wrap items-center gap-2 border-b border-border p-3">
            <div>
              <h2 className="text-sm font-medium">{filename}</h2>
              <p className="text-xs text-muted-foreground">
                Autosaves only to hosts listed in the selected variant
              </p>
            </div>
            <Badge
              aria-live="polite"
              variant={saveStatus === "Save failed" ? "destructive" : "outline"}
              className="ml-auto"
            >
              {saveStatus}
            </Badge>
            <Button
              variant="secondary"
              size="sm"
              disabled={
                !selected ||
                selected.content_sha256 === "missing" ||
                syncVariant.isPending ||
                saveStatus !== "Saved"
              }
              onClick={() => void syncToAll()}
            >
              <UploadIcon /> Sync to all hosts
            </Button>
          </header>
          {selected ? (
            <div className="flex min-h-0 flex-1 flex-col gap-2 p-3">
              <Textarea
                aria-label={`${filename} global memory`}
                value={draft}
                onChange={(event) => scheduleSave(selected, event.target.value)}
                disabled={!selected.hosts.some((host) => host.online)}
                className="min-h-[26rem] flex-1 resize-none font-mono text-sm"
                spellCheck={false}
              />
              <div className="flex justify-between gap-3 text-xs text-muted-foreground">
                <span>
                  Saving to {selected.hosts.filter((host) => host.online).length} online host(s)
                </span>
                <span>{selected.token_count.toLocaleString()} tokens</span>
              </div>
            </div>
          ) : (
            <div className="grid flex-1 place-items-center p-6 text-sm text-muted-foreground">
              Create {filename} on one host, then refresh this panel.
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
