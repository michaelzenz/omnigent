import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangleIcon, ArrowDownIcon, ArrowUpIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  useCreateMemoryCategory,
  useDeleteMemoryCategory,
  useMemory,
  useReorderMemoryCategories,
  useUpdateMemoryCategory,
  useUpdateMemorySettings,
  type MemoryCategory,
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

      <Alert>
        <AlertTitle>Omnigent harness only</AlertTitle>
        <AlertDescription>
          This memory is injected only into sessions using the Omnigent harness. Other harnesses are
          unaffected.
        </AlertDescription>
      </Alert>

      {memory.data.over_limit && (
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
    </div>
  );
}
