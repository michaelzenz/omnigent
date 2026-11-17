import { lazy, Suspense, useEffect, useState } from "react";
import { RefreshCwIcon, UploadIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import {
  type MemoryHost,
  type MemoryVariant,
  type MemoryWriteHostResult,
  type MemoryWriteTarget,
  useMemorySync,
  useWriteMemory,
} from "@/hooks/useMemorySync";
import { cn } from "@/lib/utils";

const SkillVariantDiff = lazy(() =>
  import("./SkillVariantDiff").then((module) => ({ default: module.SkillVariantDiff })),
);

const HOST_STATE_LABELS: Record<MemoryHost["state"], string> = {
  present: "Has the file",
  missing: "No file",
  offline: "Offline",
  needs_update: "Update needed",
  error: "Error",
};

/** The variant that the editor text started from. */
interface DraftBase {
  hash: string | null;
  hostIds: string[];
  content: string;
}

function hostCount(count: number): string {
  return `${count} host${count === 1 ? "" : "s"}`;
}

function variantLabel(variants: MemoryVariant[], variant: MemoryVariant): string {
  return `Variant ${variants.indexOf(variant) + 1}`;
}

function describeResults(results: MemoryWriteHostResult[], hosts: MemoryHost[], done: string) {
  const hostName = (hostId: string) =>
    hosts.find((host) => host.hostId === hostId)?.hostName ?? hostId;
  const problems = results.filter((result) => result.status !== "done");
  if (problems.length === 0) return done;
  return problems
    .map((result) =>
      result.status === "conflict"
        ? `${hostName(result.hostId)}: its file changed after you loaded it, so it kept its own text.`
        : `${hostName(result.hostId)}: ${result.error ?? "the write failed."}`,
    )
    .join(" ");
}

/**
 * ``~/AGENTS.md`` on the user's hosts. Hosts with the same file form a variant.
 * Save writes the edited text to the hosts of its variant; Sync to all hosts
 * writes it to every online host. A host takes a write only while its file is
 * still the one this tab read. Unsaved text stays until Save, Discard or Refresh.
 */
export function MemoryTab() {
  const memory = useMemorySync();
  const write = useWriteMemory();
  const [selectedHash, setSelectedHash] = useState<string | null>(null);
  const [compareHash, setCompareHash] = useState<string | null>(null);
  const [base, setBase] = useState<DraftBase | null>(null);
  const [draft, setDraft] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const variants = memory.data?.variants ?? [];
  const hosts = memory.data?.hosts ?? [];
  const writable = hosts.filter((host) => host.state === "present" || host.state === "missing");
  const skipped = hosts.filter((host) => host.state !== "present" && host.state !== "missing");
  const missingCount = hosts.filter((host) => host.state === "missing").length;
  const dirty = base !== null && draft !== base.content;
  const shown = variants.find((variant) => variant.contentSha256 === base?.hash) ?? null;
  const compare =
    variants.find(
      (variant) => variant.contentSha256 === compareHash && variant.contentSha256 !== base?.hash,
    ) ?? null;
  const busy = write.isPending || memory.isFetching;
  const nothingToSync = !dirty && writable.every((host) => host.contentSha256 === base?.hash);
  const status =
    variants.length === 0
      ? "No file yet"
      : variants.length > 1
        ? `${variants.length} variants`
        : missingCount > 0
          ? `Missing on ${hostCount(missingCount)}`
          : skipped.length > 0
            ? `Synced on ${hosts.length - skipped.length} of ${hostCount(hosts.length)}`
            : "Synced";

  useEffect(() => {
    if (!memory.data || dirty) return;
    const next =
      memory.data.variants.find((variant) => variant.contentSha256 === selectedHash) ??
      memory.data.variants[0] ??
      null;
    const nextHash = next?.contentSha256 ?? null;
    if (nextHash !== selectedHash) setSelectedHash(nextHash);
    setBase({ hash: nextHash, hostIds: next?.hostIds ?? [], content: next?.content ?? "" });
    setDraft(next?.content ?? "");
    setCompareHash(null);
  }, [memory.data, selectedHash, dirty]);

  async function writeTo(targets: MemoryWriteTarget[], done: string) {
    setMessage(null);
    try {
      const results = await write.mutateAsync({ content: draft, targets });
      const saved = results.filter((result) => result.status === "done" && result.contentSha256);
      const savedHash = saved[0]?.contentSha256;
      if (savedHash) {
        setSelectedHash(savedHash);
        setBase({ hash: savedHash, hostIds: saved.map((result) => result.hostId), content: draft });
      }
      const conflicted = results.some((result) => result.status === "conflict");
      const hint =
        !savedHash && conflicted
          ? " Compare the variants, then sync your text to all hosts or discard it."
          : "";
      setMessage(describeResults(results, hosts, done) + hint);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The write failed.");
    }
  }

  function save() {
    if (!shown || !base?.hash) return;
    const hash = base.hash;
    void writeTo(
      base.hostIds.map((hostId) => ({ hostId, expectedSha256: hash })),
      `Saved to ${hostCount(base.hostIds.length)}.`,
    );
  }

  function syncAll() {
    const replaced = writable.filter(
      (host) => host.contentSha256 !== null && host.contentSha256 !== base?.hash,
    );
    if (
      replaced.length > 0 &&
      !window.confirm(
        `Replace ~/AGENTS.md on ${replaced.map((host) => host.hostName).join(", ")} with this text?`,
      )
    ) {
      return;
    }
    const skippedNote =
      skipped.length > 0
        ? ` Skipped ${skipped
            .map((host) => `${host.hostName} (${HOST_STATE_LABELS[host.state]})`)
            .join(", ")}.`
        : "";
    void writeTo(
      writable.map((host) => ({ hostId: host.hostId, expectedSha256: host.contentSha256 })),
      `Synced to ${hostCount(writable.length)}.${skippedNote}`,
    );
  }

  function discard() {
    setDraft(base?.content ?? "");
  }

  function refresh() {
    if (dirty && !window.confirm("Discard your unsaved changes and read the hosts again?")) return;
    discard();
    setMessage(null);
    void memory.refetch();
  }

  function edit(value: string) {
    setDraft(base?.content.includes("\r\n") ? value.replace(/\r?\n/g, "\r\n") : value);
  }

  return (
    <div
      className="flex h-[calc(100vh-12rem)] min-h-[24rem] flex-col overflow-hidden rounded-lg border border-border bg-background"
      data-testid="glossaries-memory-tab"
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <div className="mr-auto min-w-0">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <code>~/AGENTS.md</code>
            {memory.data &&
              (status === "Synced" ? (
                <Badge className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                  Synced
                </Badge>
              ) : (
                <Badge variant="outline">{status}</Badge>
              ))}
          </h2>
          <p className="text-xs text-muted-foreground">
            The AGENTS.md in the home folder of each host.
          </p>
        </div>
        {variants.length > 0 && (
          <Select
            value={shown?.contentSha256 ?? ""}
            disabled={dirty || busy}
            onValueChange={setSelectedHash}
          >
            <SelectTrigger className="w-44" aria-label="Variant">
              <SelectValue placeholder="Variant" />
            </SelectTrigger>
            <SelectContent>
              {variants.map((variant) => (
                <SelectItem key={variant.contentSha256} value={variant.contentSha256}>
                  {variantLabel(variants, variant)} · {hostCount(variant.hostIds.length)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {variants.length > 1 && (
          <Select
            value={compare?.contentSha256 ?? "none"}
            onValueChange={(value) => setCompareHash(value === "none" ? null : value)}
          >
            <SelectTrigger className="w-44" aria-label="Compare with">
              <SelectValue placeholder="Compare" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">No comparison</SelectItem>
              {variants
                .filter((variant) => variant.contentSha256 !== base?.hash)
                .map((variant) => (
                  <SelectItem key={variant.contentSha256} value={variant.contentSha256}>
                    Compare {variantLabel(variants, variant)}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
        )}
        {dirty && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={discard}>
            Discard
          </Button>
        )}
        <Button
          size="sm"
          disabled={!shown || !dirty || busy}
          onClick={save}
          title="Write this text to the hosts of this variant"
          data-testid="memory-save"
        >
          {write.isPending ? "Saving…" : "Save"}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={busy || writable.length === 0 || nothingToSync}
          onClick={syncAll}
          title="Write this text to every online host"
          data-testid="memory-sync-all"
        >
          <UploadIcon /> Sync to all hosts
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Read the hosts again"
          title="Read the hosts again"
          disabled={busy}
          onClick={refresh}
        >
          <RefreshCwIcon className={cn(memory.isFetching && "animate-spin")} />
        </Button>
      </header>

      {hosts.length > 0 && (
        <ul className="flex flex-wrap gap-1.5 border-b border-border px-3 py-2" aria-label="Hosts">
          {hosts.map((host) => {
            const variant = variants.find((item) => item.contentSha256 === host.contentSha256);
            const label = variant ? variantLabel(variants, variant) : HOST_STATE_LABELS[host.state];
            return (
              <li
                key={host.hostId}
                title={host.error ?? undefined}
                className={cn(
                  "flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs",
                  variant && variant === shown && "border-primary/60 bg-primary/5",
                )}
                data-testid={`memory-host-${host.hostId}`}
              >
                <span className="font-medium">{host.hostName}</span>
                <span className="text-muted-foreground">{label}</span>
              </li>
            );
          })}
        </ul>
      )}

      <div className="flex min-h-0 flex-1 flex-col bg-muted/30 p-3">
        {memory.isPending ? (
          <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
            <span className="flex items-center gap-2">
              <Spinner className="size-4" /> Reading ~/AGENTS.md from your hosts…
            </span>
          </div>
        ) : memory.isError ? (
          <p role="alert" className="text-sm text-destructive">
            Could not read the hosts: {memory.error.message}
          </p>
        ) : compare ? (
          <Suspense
            fallback={
              <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
                Loading differences…
              </div>
            }
          >
            <SkillVariantDiff
              original={compare.content}
              modified={draft}
              originalLabel={variantLabel(variants, compare)}
              modifiedLabel={`${shown ? variantLabel(variants, shown) : "Your text"}${
                dirty ? " (unsaved)" : ""
              }`}
            />
          </Suspense>
        ) : (
          <>
            {variants.length === 0 && (
              <p className="pb-2 text-xs text-muted-foreground">
                {writable.length > 0
                  ? "No host has ~/AGENTS.md yet. Write it here, then sync it to all hosts."
                  : "No online host can use Memory yet."}
              </p>
            )}
            <Textarea
              value={draft}
              onChange={(event) => edit(event.target.value)}
              disabled={busy || writable.length === 0}
              aria-label="AGENTS.md text"
              spellCheck={false}
              className="min-h-0 flex-1 resize-none rounded-lg bg-background font-mono text-xs text-foreground"
              data-testid="memory-editor"
            />
          </>
        )}
      </div>
      {message && (
        <p role="status" className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
          {message}
        </p>
      )}
    </div>
  );
}
