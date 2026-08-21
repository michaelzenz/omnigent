import { ListTreeIcon, PlugIcon, SparklesIcon, WrenchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import {
  collectTurnActivity,
  type TurnActivityCall,
  type TurnActivityCategory,
  type TurnActivityGroup,
} from "@/lib/turnActivity";
import type { RenderItem } from "@/lib/renderItems";

const CATEGORY_META: Record<
  TurnActivityCategory,
  { label: string; icon: typeof WrenchIcon }
> = {
  tool: { label: "Tools", icon: WrenchIcon },
  skill: { label: "Skills", icon: SparklesIcon },
  mcp: { label: "MCPs", icon: PlugIcon },
};

function statusLabel(status: TurnActivityCall["status"]): string {
  if (status === "input-available") return "Running";
  if (status === "output-error") return "Failed";
  if (status === "cancelled") return "Cancelled";
  if (status === "no-output") return "No output";
  return "Completed";
}

function statusClass(status: TurnActivityCall["status"]): string {
  if (status === "output-error") return "text-destructive";
  if (status === "cancelled" || status === "no-output") return "text-amber-600 dark:text-amber-400";
  if (status === "input-available") return "text-blue-600 dark:text-blue-400";
  return "text-emerald-600 dark:text-emerald-400";
}

function formatPayload(value: Record<string, unknown> | string): string {
  let formatted: string;
  if (typeof value === "string") {
    formatted = value;
  } else {
    try {
      formatted = JSON.stringify(value, null, 2);
    } catch {
      formatted = String(value);
    }
  }
  const limit = 4_000;
  if (formatted.length <= limit) return formatted;
  return `${formatted.slice(0, limit)}\n… ${formatted.length - limit} more characters`;
}

function ActivityCallDetails({ call, index }: { call: TurnActivityCall; index: number }) {
  const hasArguments = Object.keys(call.arguments).length > 0;
  const hasDetails =
    hasArguments || call.output !== null || call.agentName !== null || call.duration !== undefined;
  const summary = (
    <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
      <span className="truncate text-xs text-muted-foreground">Call {index + 1}</span>
      <span className={cn("shrink-0 text-[10px] font-medium", statusClass(call.status))}>
        {statusLabel(call.status)}
      </span>
    </div>
  );

  if (!hasDetails) return <div className="flex px-2 py-1.5">{summary}</div>;

  return (
    <details className="group/call border-t border-border/50 first:border-t-0">
      <summary className="flex cursor-pointer list-none items-center px-2 py-1.5 marker:hidden">
        {summary}
      </summary>
      <div className="space-y-2 px-2 pb-2">
        {(call.agentName || call.duration !== undefined) && (
          <div className="flex flex-wrap gap-x-3 text-[10px] text-muted-foreground">
            {call.agentName && <span>Agent: {call.agentName}</span>}
            {call.duration !== undefined && <span>Duration: {call.duration.toFixed(1)}s</span>}
          </div>
        )}
        {hasArguments && (
          <div>
            <p className="mb-1 text-[10px] font-medium text-muted-foreground">Arguments</p>
            <pre className="max-h-32 overflow-auto rounded-md bg-muted/70 p-2 text-[10px] leading-4 whitespace-pre-wrap">
              {formatPayload(call.arguments)}
            </pre>
          </div>
        )}
        {call.output !== null && (
          <div>
            <p className="mb-1 text-[10px] font-medium text-muted-foreground">Output</p>
            <pre className="max-h-40 overflow-auto rounded-md bg-muted/70 p-2 text-[10px] leading-4 whitespace-pre-wrap">
              {formatPayload(call.output)}
            </pre>
          </div>
        )}
      </div>
    </details>
  );
}

function ActivityGroupCard({ group }: { group: TurnActivityGroup }) {
  const subtitle =
    group.category === "mcp"
      ? group.serverName
        ? `${group.serverName} · ${group.operation ?? "call"}`
        : "Server unavailable"
      : group.category === "skill" && group.operation
        ? group.operation
        : null;

  return (
    <div className="overflow-hidden rounded-lg border border-border/70 bg-background">
      <div className="flex items-start justify-between gap-2 px-2.5 py-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium" title={group.name}>
            {group.name}
          </p>
          {subtitle && <p className="truncate text-[10px] text-muted-foreground">{subtitle}</p>}
        </div>
        {group.calls.length > 1 && (
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            ×{group.calls.length}
          </span>
        )}
      </div>
      <div className="border-t border-border/50">
        {group.calls.map((call, index) => (
          <ActivityCallDetails key={call.id} call={call} index={index} />
        ))}
      </div>
    </div>
  );
}

export function TurnActivityPopover({ items }: { items: RenderItem[] }) {
  const activity = collectTurnActivity(items);
  if (activity.totalCalls === 0) return null;

  return (
    <Dialog>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <DialogTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-xxs"
                className="text-muted-foreground hover:text-foreground"
                data-testid="turn-activity-trigger"
                aria-label={`Turn activity, ${activity.totalCalls} call${
                  activity.totalCalls === 1 ? "" : "s"
                }`}
              >
                <ListTreeIcon size={14} />
              </Button>
            </DialogTrigger>
          </TooltipTrigger>
          <TooltipContent>Turn activity</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <DialogContent
        className="max-h-[min(42rem,calc(100vh-2rem))] gap-0 overflow-hidden rounded-2xl p-0 sm:max-w-[min(52rem,calc(100vw-2rem))]"
        data-testid="turn-activity-board"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3 pr-16">
          <div>
            <DialogTitle className="min-h-0 pr-0 text-sm leading-5">Turn activity</DialogTitle>
            <DialogDescription className="text-xs">
              Tools, skills, and MCP calls recorded for this turn
            </DialogDescription>
          </div>
          <span className="rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground">
            {activity.totalCalls} call{activity.totalCalls === 1 ? "" : "s"}
          </span>
        </div>
        <div className="grid max-h-[min(36rem,65vh)] grid-cols-1 gap-4 overflow-y-auto p-4 sm:grid-cols-3">
          {(Object.keys(CATEGORY_META) as TurnActivityCategory[]).map((category) => {
            const meta = CATEGORY_META[category];
            const groups = activity.groups.filter((group) => group.category === category);
            const Icon = meta.icon;
            return (
              <section key={category} aria-label={meta.label} className="min-w-0 space-y-2">
                <div className="flex items-center gap-1.5">
                  <Icon className="size-3.5 text-muted-foreground" />
                  <h4 className="text-xs font-semibold">{meta.label}</h4>
                  <span className="text-[10px] text-muted-foreground">{groups.length}</span>
                </div>
                {groups.length > 0 ? (
                  <div className="space-y-2">
                    {groups.map((group) => (
                      <ActivityGroupCard key={group.key} group={group} />
                    ))}
                  </div>
                ) : (
                  <p className="rounded-lg border border-dashed border-border/70 px-2.5 py-3 text-center text-[10px] text-muted-foreground">
                    None used
                  </p>
                )}
              </section>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
