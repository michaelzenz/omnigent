import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  RefreshCwIcon,
  SettingsIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import {
  useDeleteSkillEverywhere,
  useRefreshSkills,
  useSaveSkillVariantFiles,
  useSkillRoots,
  useSkillTree,
  useSyncSkills,
  useSyncedSkills,
  useUpdateSkillHarnessSetting,
  type AggregatedSkill,
  type SkillTreeFile,
} from "@/hooks/useSkills";
import { cn } from "@/lib/utils";

const SkillVariantDiff = lazy(() =>
  import("./SkillVariantDiff").then((module) => ({ default: module.SkillVariantDiff })),
);
const AUTOSAVE_DELAY_MS = 700;

function harnessLabel(harness: string) {
  return harness.charAt(0).toUpperCase() + harness.slice(1);
}

function fileComparisonStatus(
  selected: SkillTreeFile | null,
  baseline: SkillTreeFile | null,
): "added" | "deleted" | "changed" | "identical" {
  if (!baseline) return "added";
  if (!selected) return "deleted";
  return selected.content === baseline.content ? "identical" : "changed";
}

function SyncStatusBadge({ status }: { status: AggregatedSkill["syncStatus"] }) {
  if (status === "synced") {
    return (
      <Badge className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">Synced</Badge>
    );
  }
  if (status === "partial") {
    return (
      <Badge
        variant="outline"
        className="border-emerald-500/50 bg-emerald-500/5 text-emerald-600 dark:text-emerald-400"
      >
        Partially synced
      </Badge>
    );
  }
  return <Badge variant="destructive">Not synced</Badge>;
}

function SkillInventoryRow({
  skill,
  selected,
  onSelect,
}: {
  skill: AggregatedSkill;
  selected: boolean;
  onSelect: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className={cn("flex items-center gap-1 px-2 py-2", selected && "bg-muted")}>
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label={`Show ${skill.name} variants`}>
            {open ? <ChevronDownIcon /> : <ChevronRightIcon />}
          </Button>
        </CollapsibleTrigger>
        <button type="button" onClick={onSelect} className="min-w-0 flex-1 text-left">
          <span className="block truncate text-sm font-medium">{skill.name}</span>
          <span className="block truncate text-xs text-muted-foreground">
            {skill.description || "No description"}
          </span>
        </button>
        <SyncStatusBadge status={skill.syncStatus} />
      </div>
      <CollapsibleContent>
        <div className="space-y-3 border-t border-border bg-muted/20 px-4 py-3">
          {skill.hosts.map((host) => (
            <div key={host.hostId} className="space-y-1.5">
              <div className="flex items-center justify-between text-xs font-medium">
                <span>{host.hostName}</span>
                {!host.online && <Badge variant="outline">Offline</Badge>}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {host.harnesses.map((harness) => {
                  const variantIndex = harness.occurrence
                    ? skill.variants.findIndex(
                        (variant) => variant.contentSha256 === harness.occurrence?.contentSha256,
                      )
                    : -1;
                  const label =
                    harness.state === "present" || harness.state === "ignored_variant"
                      ? `Variant ${variantIndex + 1}`
                      : harness.state === "missing"
                        ? "Variant Missing"
                        : harness.state === "unavailable"
                          ? "Not installed"
                          : harness.state === "not_reported"
                            ? "Not reported"
                            : harness.state.charAt(0).toUpperCase() + harness.state.slice(1);
                  const badge = (
                    <Badge
                      variant={harness.state === "missing" ? "destructive" : "outline"}
                      className={cn(
                        harness.occurrence && "cursor-pointer",
                        harness.state === "unavailable" &&
                          "border-emerald-500/40 bg-emerald-500/5 text-emerald-600 dark:text-emerald-400",
                      )}
                    >
                      {harnessLabel(harness.harness)} · {label}
                    </Badge>
                  );
                  return harness.occurrence ? (
                    <Popover key={harness.harness}>
                      <PopoverTrigger asChild>
                        <button type="button">{badge}</button>
                      </PopoverTrigger>
                      <PopoverContent className="w-auto max-w-[min(36rem,calc(100vw-2rem))] p-2">
                        <code className="block overflow-x-auto whitespace-nowrap text-xs">
                          ~/{harness.occurrence.relHomePath}
                        </code>
                      </PopoverContent>
                    </Popover>
                  ) : (
                    <span key={harness.harness}>{badge}</span>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function SkillRootsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const hosts = useSkillRoots(open);
  const updateSetting = useUpdateSkillHarnessSetting();
  const [hostId, setHostId] = useState<string | null>(null);
  const selectedHost =
    hosts.data?.find((host) => host.hostId === hostId) ?? hosts.data?.[0] ?? null;

  useEffect(() => {
    if (selectedHost && selectedHost.hostId !== hostId) setHostId(selectedHost.hostId);
  }, [hostId, selectedHost]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] w-[calc(100vw-2rem)] overflow-y-auto sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle>Skill search locations</DialogTitle>
          <DialogDescription>
            Directories this host searches independently for each harness.
          </DialogDescription>
        </DialogHeader>
        {hosts.data && hosts.data.length > 1 && (
          <Select value={selectedHost?.hostId ?? ""} onValueChange={setHostId}>
            <SelectTrigger className="w-64">
              <SelectValue placeholder="Select host" />
            </SelectTrigger>
            <SelectContent>
              {hosts.data.map((host) => (
                <SelectItem key={host.hostId} value={host.hostId}>
                  {host.hostName}
                  {!host.online ? " · Offline" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {selectedHost?.error ? (
          <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            {selectedHost.error}
          </p>
        ) : (
          <div className="divide-y divide-border rounded-lg border border-border">
            {["claude", "codex", "cursor"].map((harness) => {
              const paths = selectedHost?.roots.filter((root) => root.harness === harness) ?? [];
              const globallyEnabled =
                hosts.data?.every((host) => host.syncHarnesses?.[harness] === true) ?? false;
              return (
                <section
                  key={harness}
                  className="grid min-w-0 gap-3 p-3 sm:grid-cols-[7rem_minmax(0,1fr)]"
                >
                  {harness === "cursor" && (
                    <p className="text-xs text-muted-foreground sm:col-span-2">
                      cursor can read claude skills, syncing cursor is unnecessary
                    </p>
                  )}
                  <div className="flex items-center gap-2 sm:block">
                    <h3 className="text-sm font-medium">{harnessLabel(harness)}</h3>
                    {selectedHost?.installedHarnesses[harness] ? (
                      <label className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
                        <Switch
                          checked={globallyEnabled}
                          disabled={updateSetting.isPending}
                          aria-label={`Include ${harnessLabel(harness)} in skill sync`}
                          onCheckedChange={(enabled) =>
                            void updateSetting.mutateAsync({
                              harness,
                              enabled,
                            })
                          }
                        />
                        Sync
                      </label>
                    ) : (
                      <Badge variant="outline" className="mt-1.5">
                        Not installed
                      </Badge>
                    )}
                  </div>
                  <div className="space-y-1.5">
                    {paths.map((root) => (
                      <code
                        key={root.relHomePath}
                        className="block overflow-x-auto whitespace-nowrap rounded-md border border-border bg-muted/30 px-2 py-1.5 text-xs"
                      >
                        ~/{root.relHomePath}
                      </code>
                    ))}
                    {!hosts.isLoading && paths.length === 0 && (
                      <p className="text-xs text-muted-foreground">No search locations reported.</p>
                    )}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function SkillsTab() {
  const skills = useSyncedSkills();
  const refresh = useRefreshSkills();
  const save = useSaveSkillVariantFiles();
  const sync = useSyncSkills();
  const deleteSkill = useDeleteSkillEverywhere();
  const [search, setSearch] = useState("");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [selectedVariantHash, setSelectedVariantHash] = useState<string | null>(null);
  const [compareVariantHash, setCompareVariantHash] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saveStatus, setSaveStatus] = useState<"Saved" | "Unsaved" | "Saving" | "Save failed">(
    "Saved",
  );
  const [message, setMessage] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRevision = useRef(0);
  const selectedOccurrenceRef = useRef<string | null>(null);
  const selectedSkillNameRef = useRef<string | null>(null);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (skills.data ?? []).filter(
      (skill) =>
        !query ||
        skill.name.toLowerCase().includes(query) ||
        skill.description.toLowerCase().includes(query),
    );
  }, [search, skills.data]);
  const selected = useMemo(
    () => (skills.data ?? []).find((skill) => skill.name === selectedName) ?? null,
    [selectedName, skills.data],
  );
  const firstVariantHash = selected?.variants[0]?.contentSha256 ?? null;
  const selectedVariant =
    selected?.variants.find((variant) => variant.contentSha256 === selectedVariantHash) ??
    selected?.variants[0] ??
    null;
  const onlineOccurrences = selectedVariant?.occurrences.filter((item) => item.online) ?? [];
  const selectedOccurrence = onlineOccurrences[0] ?? null;
  const compareVariant =
    selected?.variants.find((variant) => variant.contentSha256 === compareVariantHash) ?? null;
  const compareOccurrence = compareVariant?.occurrences.find((item) => item.online) ?? null;

  useEffect(() => {
    if (!selectedName && filtered[0]) setSelectedName(filtered[0].name);
  }, [filtered, selectedName]);
  useEffect(() => {
    const skillChanged = selectedSkillNameRef.current !== (selected?.name ?? null);
    selectedSkillNameRef.current = selected?.name ?? null;
    const trackedOccurrence = skillChanged ? null : selectedOccurrenceRef.current;
    const trackedVariant = trackedOccurrence
      ? selected?.variants.find((variant) =>
          variant.occurrences.some(
            (occurrence) => `${occurrence.hostId}\0${occurrence.harness}` === trackedOccurrence,
          ),
        )
      : null;
    const nextVariant = trackedVariant ?? selected?.variants[0] ?? null;
    selectedOccurrenceRef.current = nextVariant?.occurrences[0]
      ? `${nextVariant.occurrences[0].hostId}\0${nextVariant.occurrences[0].harness}`
      : null;
    setSelectedVariantHash(nextVariant?.contentSha256 ?? firstVariantHash);
    setCompareVariantHash(null);
  }, [firstVariantHash, selected?.name, selected?.variants]);
  useEffect(() => {
    setCompareVariantHash(null);
  }, [selectedVariantHash]);

  const tree = useSkillTree(
    selected?.name ?? null,
    selectedOccurrence?.hostId ?? null,
    selectedOccurrence?.harness ?? null,
  );
  const compareTree = useSkillTree(
    selected?.name ?? null,
    compareOccurrence?.hostId ?? null,
    compareOccurrence?.harness ?? null,
  );
  const editableDraftSnapshot = JSON.stringify(
    Object.fromEntries(
      (tree.data ?? []).filter((file) => !file.binary).map((file) => [file.path, file.content]),
    ),
  );
  const comparedFiles = useMemo(() => {
    const selectedFiles = new Map((tree.data ?? []).map((file) => [file.path, file]));
    const baselineFiles = new Map((compareTree.data ?? []).map((file) => [file.path, file]));
    return [...new Set([...selectedFiles.keys(), ...baselineFiles.keys()])]
      .sort((left, right) => {
        if (left === "SKILL.md") return -1;
        if (right === "SKILL.md") return 1;
        return left.localeCompare(right);
      })
      .map((path) => ({
        path,
        selected: selectedFiles.get(path) ?? null,
        baseline: baselineFiles.get(path) ?? null,
      }))
      .filter(
        ({ selected: selectedFile, baseline }) =>
          fileComparisonStatus(selectedFile, baseline) !== "identical",
      );
  }, [compareTree.data, tree.data]);
  useEffect(() => {
    setDrafts(JSON.parse(editableDraftSnapshot) as Record<string, string>);
    setSaveStatus("Saved");
  }, [editableDraftSnapshot, selectedVariant?.contentSha256, selected?.name]);
  useEffect(
    () => () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    },
    [],
  );

  function scheduleSave(path: string, content: string) {
    if (!selected || !selectedVariant) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const revision = ++saveRevision.current;
    const nextDrafts = { ...drafts, [path]: content };
    setDrafts(nextDrafts);
    setSaveStatus("Unsaved");
    const files = Object.fromEntries(
      (tree.data ?? [])
        .filter((file) => !file.binary && nextDrafts[file.path] !== file.content)
        .map((file) => [file.path, nextDrafts[file.path] ?? file.content]),
    );
    if (Object.keys(files).length === 0) {
      setSaveStatus("Saved");
      return;
    }
    const targetName = selected.name;
    const targetHash = selectedVariant.contentSha256;
    saveTimer.current = setTimeout(() => {
      saveTimer.current = null;
      setSaveStatus("Saving");
      void save
        .mutateAsync({
          name: targetName,
          contentSha256: targetHash,
          files,
        })
        .then(() => {
          if (saveRevision.current === revision) setSaveStatus("Saved");
        })
        .catch((error) => {
          if (saveRevision.current === revision) setSaveStatus("Save failed");
          setMessage(error instanceof Error ? error.message : "Save failed");
        });
    }, AUTOSAVE_DELAY_MS);
  }

  async function handleSync() {
    if (!selected || !selectedOccurrence) return;
    setMessage(null);
    try {
      await sync.mutateAsync({
        name: selected.name,
        sourceHostId: selectedOccurrence.hostId,
        sourceHarness: selectedOccurrence.harness,
      });
      setMessage("Selected variant sent to every occurrence.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Sync failed");
    }
  }

  async function handleDelete() {
    if (!selected) return;
    if (!window.confirm(`Delete "${selected.name}" from every online host and harness?`)) return;
    await deleteSkill.mutateAsync(selected.name);
    setSelectedName(null);
  }

  async function handleRefresh() {
    setMessage(null);
    try {
      await refresh.mutateAsync();
      await skills.refetch();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Skill refresh failed");
    }
  }

  return (
    <div
      className="grid min-h-[38rem] grid-cols-1 overflow-hidden rounded-lg border border-border bg-background lg:grid-cols-[22rem_minmax(0,1fr)]"
      data-testid="glossaries-skills-tab"
    >
      <section className="flex min-h-0 flex-col border-b border-border lg:border-r lg:border-b-0">
        <div className="space-y-2 border-b border-border p-3">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-medium">Skills</h2>
              <p className="text-xs text-muted-foreground">
                Variants reported independently by every host and harness.
              </p>
            </div>
            <div className="flex items-center">
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Skill search settings"
                onClick={() => setSettingsOpen(true)}
              >
                <SettingsIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Refresh skills from hosts"
                title="Refresh skills from hosts"
                disabled={refresh.isPending}
                onClick={() => void handleRefresh()}
              >
                <RefreshCwIcon className={cn(refresh.isPending && "animate-spin")} />
              </Button>
            </div>
          </div>
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search skills"
          />
        </div>
        <div className="min-h-0 flex-1 divide-y divide-border overflow-y-auto">
          {filtered.map((skill) => (
            <SkillInventoryRow
              key={skill.name}
              skill={skill}
              selected={skill.name === selected?.name}
              onSelect={() => setSelectedName(skill.name)}
            />
          ))}
          {!skills.isLoading && filtered.length === 0 && (
            <p className="p-6 text-center text-sm text-muted-foreground">No skills reported.</p>
          )}
        </div>
      </section>

      <section className="flex min-h-0 min-w-0 flex-col">
        {!selected ? (
          <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
            Select a skill to view or edit it.
          </div>
        ) : (
          <>
            <header className="flex flex-wrap items-center gap-2 border-b border-border p-3">
              <div className="mr-auto min-w-0">
                <h2 className="truncate font-medium">{selected.name}</h2>
                <p className="truncate text-xs text-muted-foreground">{selected.description}</p>
              </div>
              <Select
                value={selectedVariant?.contentSha256 ?? ""}
                disabled={saveStatus !== "Saved"}
                onValueChange={(value) => {
                  const variant = selected.variants.find(
                    (candidate) => candidate.contentSha256 === value,
                  );
                  const occurrence = variant?.occurrences[0];
                  selectedOccurrenceRef.current = occurrence
                    ? `${occurrence.hostId}\0${occurrence.harness}`
                    : null;
                  setSelectedVariantHash(value);
                }}
              >
                <SelectTrigger className="w-36">
                  <SelectValue placeholder="Variant" />
                </SelectTrigger>
                <SelectContent>
                  {selected.variants.map((variant, index) => (
                    <SelectItem key={variant.contentSha256} value={variant.contentSha256}>
                      Variant {index + 1}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={compareVariantHash ?? "none"}
                onValueChange={(value) => setCompareVariantHash(value === "none" ? null : value)}
              >
                <SelectTrigger className="w-40">
                  <SelectValue placeholder="Compare variant" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">No comparison</SelectItem>
                  {selected.variants
                    .filter((variant) => variant.contentSha256 !== selectedVariant?.contentSha256)
                    .map((variant) => {
                      const index = selected.variants.indexOf(variant);
                      return (
                        <SelectItem key={variant.contentSha256} value={variant.contentSha256}>
                          Compare Variant {index + 1}
                        </SelectItem>
                      );
                    })}
                </SelectContent>
              </Select>
              <Badge
                aria-live="polite"
                variant={saveStatus === "Save failed" ? "destructive" : "outline"}
              >
                {saveStatus}
              </Badge>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => void handleSync()}
                disabled={!selectedOccurrence || sync.isPending || saveStatus !== "Saved"}
              >
                <UploadIcon /> Sync to all
              </Button>
              <Button variant="ghost" size="icon-sm" onClick={() => void handleDelete()}>
                <Trash2Icon />
              </Button>
            </header>

            {compareVariant ? (
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-slate-100 p-3">
                {tree.isLoading || compareTree.isLoading ? (
                  <div className="grid min-h-[32rem] place-items-center text-sm text-muted-foreground">
                    Loading differences…
                  </div>
                ) : comparedFiles.length === 0 ? (
                  <div className="grid min-h-[20rem] place-items-center rounded-xl border border-slate-300 bg-white text-sm text-slate-500">
                    No file differences.
                  </div>
                ) : (
                  comparedFiles.map(({ path, selected: selectedFile, baseline }) => {
                    const status = fileComparisonStatus(selectedFile, baseline);
                    return (
                      <section
                        key={path}
                        className="overflow-hidden rounded-xl border border-slate-300 bg-white text-slate-950 shadow-sm"
                      >
                        <div className="flex items-center gap-2 border-b border-slate-300 bg-white px-4 py-3">
                          <code className="text-xs font-medium">{path}</code>
                          <Badge
                            variant={status === "deleted" ? "destructive" : "outline"}
                            className={cn(
                              "ml-auto capitalize",
                              status === "added" &&
                                "border-emerald-500/50 text-emerald-600 dark:text-emerald-400",
                              status === "changed" &&
                                "border-amber-500/50 text-amber-600 dark:text-amber-400",
                            )}
                          >
                            {status}
                          </Badge>
                        </div>
                        <Suspense
                          fallback={
                            <div className="grid min-h-[22rem] place-items-center text-sm text-muted-foreground">
                              Loading differences…
                            </div>
                          }
                        >
                          <SkillVariantDiff
                            original={baseline?.content ?? ""}
                            modified={
                              selectedFile
                                ? (drafts[selectedFile.path] ?? selectedFile.content)
                                : ""
                            }
                            originalLabel={`Variant ${
                              selected.variants.indexOf(compareVariant) + 1
                            }`}
                            modifiedLabel={`Variant ${
                              selectedVariant ? selected.variants.indexOf(selectedVariant) + 1 : "—"
                            }`}
                          />
                        </Suspense>
                      </section>
                    );
                  })
                )}
              </div>
            ) : (
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-slate-100 p-3">
                <div className="px-1 text-xs text-muted-foreground">
                  Variant {selectedVariant ? selected.variants.indexOf(selectedVariant) + 1 : "—"}
                </div>
                {(tree.data ?? []).map((file) => (
                  <section
                    key={file.path}
                    className="overflow-hidden rounded-xl border border-slate-300 bg-white text-slate-950 shadow-sm"
                  >
                    <div className="flex items-center gap-2 border-b border-slate-300 bg-white px-4 py-3">
                      <code className="text-xs font-medium">{file.path}</code>
                      {file.binary && (
                        <Badge variant="outline" className="ml-auto">
                          Binary
                        </Badge>
                      )}
                    </div>
                    {!file.binary ? (
                      <Textarea
                        value={drafts[file.path] ?? file.content}
                        onChange={(event) => scheduleSave(file.path, event.target.value)}
                        disabled={!selectedOccurrence || tree.isLoading || saveStatus === "Saving"}
                        className="min-h-[32rem] resize-none rounded-none border-0 bg-white font-mono text-xs text-slate-950 focus-visible:ring-0"
                        spellCheck={false}
                      />
                    ) : (
                      <pre className="overflow-x-auto whitespace-pre-wrap bg-white p-3 font-mono text-xs text-slate-950">
                        {file.content}
                      </pre>
                    )}
                  </section>
                ))}
              </div>
            )}
            {message && (
              <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
                {message}
              </p>
            )}
          </>
        )}
      </section>
      <SkillRootsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  );
}
