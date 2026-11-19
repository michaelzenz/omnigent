import { cn } from "@/lib/utils";
import { PRIORITY_COLORS } from "./priorityColors";
import type { BoardPriorityFilter } from "./usePriorityFilter";

const CHIP_CLASSES =
  "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border-[1.5px] px-2.5 text-xs font-medium whitespace-nowrap transition-colors";
const UNPICKED_CLASSES = "border-border text-muted-foreground hover:bg-muted hover:text-foreground";

/**
 * Chips that pick the one priority that the board shows, P0 to P3, in the
 * colors of the cards' priority menus, each with its count of live tasks. All
 * shows every task, and so does a click on the picked priority.
 */
export function PriorityFilter({
  value,
  counts,
  onChange,
}: {
  value: BoardPriorityFilter;
  /** How many live tasks have each priority, P0 first, once the board loads. */
  counts?: readonly number[];
  onChange: (value: BoardPriorityFilter) => void;
}) {
  const total = counts?.reduce((sum, count) => sum + count, 0);
  return (
    <div
      role="group"
      aria-label="Filter tasks by priority"
      className="flex min-w-0 items-center gap-1.5 overflow-x-auto"
      data-testid="globalhub-priority-filter"
    >
      {PRIORITY_COLORS.map(({ value: priority, pill, dot }) => {
        const picked = value === priority;
        return (
          <button
            key={priority}
            type="button"
            aria-pressed={picked}
            aria-label={chipLabel(`P${priority}`, counts?.[priority])}
            onClick={() => onChange(picked ? null : priority)}
            className={cn(CHIP_CLASSES, picked ? cn(pill, "font-semibold") : UNPICKED_CLASSES)}
          >
            <span aria-hidden className={cn("size-2 shrink-0 rounded-full", dot)} />P{priority}
            <ChipCount count={counts?.[priority]} />
          </button>
        );
      })}
      <button
        type="button"
        aria-pressed={value === null}
        aria-label={chipLabel("All", total)}
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
      </button>
    </div>
  );
}

function ChipCount({ count }: { count: number | undefined }) {
  if (count === undefined) return null;
  return <span className="font-normal tabular-nums opacity-70">{count}</span>;
}

function chipLabel(name: string, count: number | undefined): string {
  return count === undefined ? name : `${name}, ${count} ${count === 1 ? "task" : "tasks"}`;
}
