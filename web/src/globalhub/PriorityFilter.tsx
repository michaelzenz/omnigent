import type { BoardPriorityCounts } from "@/hooks/useGlobalHubTasks";
import { cn } from "@/lib/utils";
import { PRIORITY_COLORS } from "./priorityColors";
import type { BoardPriorityFilter } from "./usePriorityFilter";

const CHIP_CLASSES =
  "relative inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border-[1.5px] px-2.5 text-xs font-medium whitespace-nowrap transition-colors";
const UNPICKED_CLASSES = "border-border text-muted-foreground hover:bg-muted hover:text-foreground";

/**
 * Chips that pick the one priority that the board shows, P0 to P3, in the
 * colors of the cards' priority menus, each with its count of live tasks and a
 * dot while one of those tasks has news. All shows every task, and so does a
 * click on the picked priority.
 */
export function PriorityFilter({
  value,
  counts,
  onChange,
}: {
  value: BoardPriorityFilter;
  /** How many live tasks have each priority, and how many have news, once the board loads. */
  counts?: BoardPriorityCounts;
  onChange: (value: BoardPriorityFilter) => void;
}) {
  const total = counts && sum(counts.tasks);
  const totalUpdates = counts ? sum(counts.updates) : 0;
  return (
    <div
      role="group"
      aria-label="Filter tasks by priority"
      className="flex min-w-0 items-center gap-1.5 overflow-x-auto py-1 pr-1"
      data-testid="globalhub-priority-filter"
    >
      {PRIORITY_COLORS.map(({ value: priority, pill, dot }) => {
        const picked = value === priority;
        const updates = counts?.updates[priority] ?? 0;
        return (
          <button
            key={priority}
            type="button"
            aria-pressed={picked}
            aria-label={chipLabel(`P${priority}`, counts?.tasks[priority], updates)}
            title={updateTitle(updates)}
            onClick={() => onChange(picked ? null : priority)}
            className={cn(CHIP_CLASSES, picked ? cn(pill, "font-semibold") : UNPICKED_CLASSES)}
          >
            <span aria-hidden className={cn("size-2 shrink-0 rounded-full", dot)} />P{priority}
            <ChipCount count={counts?.tasks[priority]} />
            <UpdateDot updates={updates} />
          </button>
        );
      })}
      <button
        type="button"
        aria-pressed={value === null}
        aria-label={chipLabel("All", total, totalUpdates)}
        title={updateTitle(totalUpdates)}
        onClick={() => onChange(null)}
        className={cn(
          CHIP_CLASSES,
          value === null
            ? "border-foreground/25 bg-muted font-semibold text-foreground"
            : UNPICKED_CLASSES,
        )}
      >
        All
        <ChipCount count={total} />
        <UpdateDot updates={totalUpdates} />
      </button>
    </div>
  );
}

function ChipCount({ count }: { count: number | undefined }) {
  if (count === undefined) return null;
  return <span className="font-normal tabular-nums opacity-70">{count}</span>;
}

function UpdateDot({ updates }: { updates: number }) {
  if (updates === 0) return null;
  return (
    <span
      aria-hidden
      className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-brand-accent ring-2 ring-background"
      data-testid="globalhub-priority-update-dot"
    />
  );
}

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function chipLabel(name: string, count: number | undefined, updates: number): string {
  if (count === undefined) return name;
  const label = `${name}, ${count} ${count === 1 ? "task" : "tasks"}`;
  return updates > 0
    ? `${label}, ${updates} with ${updates === 1 ? "an update" : "updates"}`
    : label;
}

function updateTitle(updates: number): string | undefined {
  if (updates === 0) return undefined;
  return `${updates} ${updates === 1 ? "task has" : "tasks have"} an update`;
}
