import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import type { FyiClusterCard as FyiClusterCardType } from "@/lib/agentTasksApi";
import { useResolveFyiCluster } from "@/hooks/useBoardDecisions";

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
      className="rounded-md border border-border/70 bg-muted/20 p-3"
      data-testid={`fyi-card-${card.id}`}
    >
      <header className="mb-2">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">FYI</p>
        <h3 className="text-sm font-medium leading-tight text-foreground">{card.headline}</h3>
        <p className="text-xs text-muted-foreground">{signalLabel}</p>
      </header>

      {card.rationale ? (
        <p className="mb-2 text-xs text-muted-foreground">{card.rationale}</p>
      ) : null}

      <div className="mb-3">
        <button
          type="button"
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
          onClick={() => setShowEvents((open) => !open)}
        >
          {showEvents ? "Hide signals" : "Show signals"}
        </button>
        {showEvents ? (
          <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
            {card.body.events.map((event) => (
              <li key={event.id} className="truncate">
                {event.title}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <footer className="flex justify-end gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={resolveFyi.isPending}
          onClick={() => void resolveFyi.mutateAsync({ clusterId: card.id, resolution: "dismiss_fyi" })}
        >
          Dismiss
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={resolveFyi.isPending}
          onClick={() =>
            void resolveFyi.mutateAsync({
              clusterId: card.id,
              resolution: "promote_to_routing",
              routingTitle: card.headline,
            })
          }
        >
          Route anyway
        </Button>
      </footer>
    </article>
  );
}
