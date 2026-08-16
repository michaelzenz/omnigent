import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { useScriptPluginHealth } from "@/hooks/useScriptPluginHealth";
import type { ScriptPluginHealthRow, ScriptPluginKind } from "@/lib/agentTasksApi";

interface ScriptPluginsBoardProps {
  kind: ScriptPluginKind;
  testId: string;
}

/** A row in the health board, after status derivation. */
interface BoardRow {
  host_id: string;
  name: string;
  status: HealthStatus;
  last_run_at: number | null;
  last_failure_at: number | null;
  consecutive_failures: number;
  last_error: string | null;
  singleton_skipped: boolean;
  warning: string | null;
  interval_s: number | null;
  fire_at: number | null;
  fired_at: number | null;
  updated_at: number;
}

type HealthStatus =
  | "ok"
  | "failing"
  | "stale"
  | "unknown"
  | "skipped"
  // timer-only:
  | "fired"
  | "past_due"
  | "scheduled";

const POLL_STATUS: Record<string, HealthStatus> = {
  ok: "ok",
  exit_nonzero: "failing",
  timeout: "failing",
  start_failed: "failing",
  skipped_singleton: "skipped",
  skipped_config: "failing",
};

const TIMER_FAIL = new Set(["exit_nonzero", "timeout", "start_failed", "skipped_config"]);

function deriveStatus(row: ScriptPluginHealthRow, nowMs: number): HealthStatus {
  if (row.singleton_skipped) return "skipped";
  if (row.outcome === "skipped_singleton") return "skipped";
  if (row.kind === "timer") {
    if (TIMER_FAIL.has(row.outcome)) return "failing";
    const fireAt = row.fire_at ? row.fire_at * 1000 : null;
    const firedAt = row.fired_at ? row.fired_at * 1000 : null;
    if (fireAt !== null && firedAt !== null && firedAt >= fireAt) return "fired";
    if (fireAt !== null && nowMs < fireAt) return "scheduled";
    if (fireAt !== null && nowMs >= fireAt) return "past_due";
    return "unknown";
  }
  // poll
  const status = POLL_STATUS[row.outcome];
  if (status === undefined) return "unknown";
  if (status === "ok") {
    // healthy unless we haven't heard from this host in > 3x heartbeat.
    const updatedMs = row.updated_at * 1000;
    if (nowMs - updatedMs > 9 * 60 * 1000) return "stale";
    return "ok";
  }
  return status;
}

const STATUS_META: Record<HealthStatus, { label: string; className: string }> = {
  ok: { label: "ok", className: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  failing: { label: "failing", className: "bg-destructive/15 text-destructive" },
  stale: { label: "stale", className: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  unknown: { label: "unknown", className: "bg-muted text-muted-foreground" },
  skipped: { label: "skipped", className: "bg-muted text-muted-foreground" },
  fired: { label: "fired", className: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  past_due: { label: "past due", className: "bg-destructive/15 text-destructive" },
  scheduled: { label: "scheduled", className: "bg-sky-500/15 text-sky-600 dark:text-sky-400" },
};

function StatusPill({ status }: { status: HealthStatus }) {
  const meta = STATUS_META[status];
  return <Badge className={cn("border-transparent", meta.className)}>{meta.label}</Badge>;
}

function formatTime(epoch: number | null): string {
  if (epoch === null) return "—";
  const d = new Date(epoch * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function relative(epoch: number | null, nowMs: number): string {
  if (epoch === null) return "—";
  const secs = Math.round((nowMs - epoch * 1000) / 1000);
  if (secs < 0) return "in " + humanize(-secs);
  return humanize(secs) + " ago";
}

function humanize(secs: number): string {
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  return `${Math.floor(secs / 3600)}h`;
}

function PluginRow({ row, nowMs, kind }: { row: BoardRow; nowMs: number; kind: ScriptPluginKind }) {
  const [open, setOpen] = useState(false);
  const hasError = Boolean(row.last_error);
  const hasWarning = Boolean(row.warning);
  const detail = hasError ? "error" : hasWarning ? "warn" : null;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div
        className="grid items-center gap-2 px-3 py-2 text-sm odd:bg-muted/30"
        style={{ gridTemplateColumns: "minmax(0,1fr) auto auto auto" }}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="truncate font-medium">{row.name}</span>
            {hasWarning && (
              <span
                title={row.warning ?? ""}
                aria-label={row.warning ?? "warning"}
                className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-[10px] text-amber-600 dark:text-amber-400"
              >
                ⚠
              </span>
            )}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            last run {formatTime(row.last_run_at)}
          </div>
        </div>
        <div className="text-xs text-muted-foreground tabular-nums">
          {kind === "poll"
            ? row.interval_s
              ? `${Math.round(row.interval_s)}s`
              : "—"
            : row.fire_at
              ? formatTime(row.fire_at)
              : "—"}
        </div>
        <div className="text-xs text-muted-foreground tabular-nums">
          {kind === "timer" ? formatTime(row.fired_at) : relative(row.last_failure_at, nowMs)}
        </div>
        <div className="flex items-center gap-2">
          {row.consecutive_failures > 0 && (
            <span className="text-xs text-destructive tabular-nums">
              ×{row.consecutive_failures}
            </span>
          )}
          <StatusPill status={row.status} />
          {detail && (
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="text-xs text-muted-foreground underline-offset-2 hover:underline"
                aria-expanded={open}
              >
                {open ? "hide" : detail}
              </button>
            </CollapsibleTrigger>
          )}
        </div>
      </div>
      {detail && (
        <CollapsibleContent>
          {hasError && (
            <pre className="mx-3 mb-2 max-h-40 overflow-auto rounded bg-destructive/10 p-2 text-xs text-destructive whitespace-pre-wrap">
              {row.last_error}
            </pre>
          )}
          {hasWarning && (
            <pre className="mx-3 mb-2 max-h-40 overflow-auto rounded bg-amber-500/10 p-2 text-xs text-amber-600 dark:text-amber-400 whitespace-pre-wrap">
              {row.warning}
            </pre>
          )}
        </CollapsibleContent>
      )}
    </Collapsible>
  );
}

function HostGroup({ hostId, rows, nowMs, kind }: { hostId: string; rows: BoardRow[]; nowMs: number; kind: ScriptPluginKind }) {
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="font-mono text-xs text-muted-foreground">{hostId}</span>
        <span className="text-xs text-muted-foreground">{rows.length} plugin{rows.length === 1 ? "" : "s"}</span>
      </div>
      <div>
        {rows.map((row) => (
          <PluginRow key={`${row.host_id}:${row.name}`} row={row} nowMs={nowMs} kind={kind} />
        ))}
      </div>
    </Card>
  );
}

export function ScriptPluginsBoard({ kind, testId }: ScriptPluginsBoardProps) {
  const query = useScriptPluginHealth(kind);
  const nowMs = Date.now();

  const grouped = useMemo(() => {
    const rows: BoardRow[] = (query.data ?? []).map((r) => ({
      host_id: r.host_id,
      name: r.name,
      status: deriveStatus(r, nowMs),
      last_run_at: r.last_run_at,
      last_failure_at: r.last_failure_at,
      consecutive_failures: r.consecutive_failures,
      last_error: r.last_error,
      singleton_skipped: r.singleton_skipped,
      warning: r.warning,
      interval_s: r.interval_s,
      fire_at: r.fire_at,
      fired_at: r.fired_at,
      updated_at: r.updated_at,
    }));
    const byHost = new Map<string, BoardRow[]>();
    for (const row of rows) {
      const list = byHost.get(row.host_id) ?? [];
      list.push(row);
      byHost.set(row.host_id, list);
    }
    return Array.from(byHost.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [query.data, nowMs]);

  const headerColumns = kind === "poll"
    ? ["plugin", "interval", "last fail", "status"]
    : ["plugin", "fire at", "fired at", "status"];

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-3" data-testid={testId}>
      <div
        className="grid gap-2 px-3 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground"
        style={{ gridTemplateColumns: "minmax(0,1fr) auto auto auto" }}
      >
        {headerColumns.map((c) => (
          <span key={c} className={c === "status" ? "text-right" : ""}>
            {c}
          </span>
        ))}
      </div>
      {query.isLoading ? (
        <div className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          Loading plugin health…
        </div>
      ) : grouped.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          No {kind === "poll" ? "poll" : "timer"} plugins reporting yet. Hosts post a snapshot on
          change and roughly every 3 minutes.
        </div>
      ) : (
        grouped.map(([hostId, rows]) => (
          <HostGroup key={hostId} hostId={hostId} rows={rows} nowMs={nowMs} kind={kind} />
        ))
      )}
    </div>
  );
}
