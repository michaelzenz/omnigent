import { Loader2Icon } from "lucide-react";
import { useBoardTriage } from "@/hooks/useBoardDecisions";
import { FyiClusterCard } from "./FyiClusterCard";

export function BoardFyiStream() {
  const { data: triage, isLoading, error } = useBoardTriage();
  const cards = triage?.fyi ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" aria-hidden />
        Loading FYI…
      </div>
    );
  }

  if (error) {
    return <p className="text-sm text-destructive">Failed to load FYI signals.</p>;
  }

  if (!cards.length) {
    return null;
  }

  const signalCount = cards.reduce((sum, card) => sum + card.body.events.length, 0);

  return (
    <section className="space-y-3" data-testid="board-fyi-stream">
      <h2 className="text-sm font-medium text-foreground">
        FYI
        <span className="ml-2 text-muted-foreground">
          ({signalCount} signal{signalCount === 1 ? "" : "s"})
        </span>
      </h2>
      <div className="flex flex-col gap-3">
        {cards.map((card) => (
          <FyiClusterCard key={card.id} card={card} />
        ))}
      </div>
    </section>
  );
}
