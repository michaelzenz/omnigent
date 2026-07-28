import { useMemo, useState } from "react";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { FyiClusterCard as FyiClusterCardType } from "@/lib/agentTasksApi";
import { useResolveFyiCluster } from "@/hooks/useBoardTriage";

interface FyiClusterCardProps {
  card: FyiClusterCardType;
}

export function FyiClusterCard({ card }: FyiClusterCardProps) {
  const resolveFyi = useResolveFyiCluster();
  const [showEvents, setShowEvents] = useState(true);

  const signalLabel = useMemo(() => {
    const count = card.body.events.length;
    return count === 1 ? "1 signal" : `${count} signals`;
  }, [card.body.events.length]);

  return (
    <article
      className="rounded-md border border-border bg-background p-3 shadow-sm"
      data-testid={`fyi-card-${card.id}`}
    >
      <header className="mb-2 border-l-2 border-foreground/25 pl-2.5">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-foreground/55">
          FYI
        </p>
        <h3 className="text-sm font-semibold leading-snug text-foreground">{card.headline}</h3>
        <p className="mt-0.5 text-xs font-medium text-foreground/70">{signalLabel}</p>
      </header>

      {card.rationale ? (
        <p className="mb-3 text-sm leading-relaxed text-foreground/85">{card.rationale}</p>
      ) : null}

      <div className="mb-3 rounded-md border border-border/70 bg-muted/35">
        <button
          type="button"
          className="flex w-full items-center gap-1.5 px-2.5 py-2 text-left text-xs font-medium text-foreground/75 hover:text-foreground"
          onClick={() => setShowEvents((open) => !open)}
        >
          {showEvents ? (
            <ChevronDownIcon className="size-3.5 shrink-0" aria-hidden />
          ) : (
            <ChevronRightIcon className="size-3.5 shrink-0" aria-hidden />
          )}
          {showEvents ? "Hide signals" : "Show signals"}
        </button>
        {showEvents ? (
          <ul className="space-y-0 border-t border-border/60 px-2.5 py-2">
            {card.body.events.map((event) => (
              <li
                key={event.id}
                className={cn(
                  "flex items-start gap-2 py-1 text-sm text-foreground",
                  "first:pt-0 last:pb-0",
                )}
              >
                <span
                  className="mt-2 size-1.5 shrink-0 rounded-full bg-foreground/45"
                  aria-hidden
                />
                <span className="min-w-0 leading-snug">{event.title}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <footer className="flex justify-end border-t border-border/50 pt-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={resolveFyi.isPending}
          onClick={() => void resolveFyi.mutateAsync({ clusterId: card.id, resolution: "dismiss_fyi" })}
        >
          Dismiss
        </Button>
      </footer>
    </article>
  );
}
