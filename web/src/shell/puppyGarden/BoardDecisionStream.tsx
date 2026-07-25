import { Loader2Icon } from "lucide-react";
import { useBoardDecisions } from "@/hooks/useBoardDecisions";
import { TaskItemRoutingCard } from "./TaskItemRoutingCard";

export function BoardDecisionStream() {
  const { data: cards, isLoading, error } = useBoardDecisions();

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" aria-hidden />
        Loading decisions…
      </div>
    );
  }

  if (error) {
    return <p className="text-sm text-destructive">Failed to load board decisions.</p>;
  }

  if (!cards?.length) {
    return null;
  }

  return (
    <section className="space-y-3" data-testid="board-decision-stream">
      <h2 className="text-sm font-medium text-foreground">
        Decisions
        <span className="ml-2 text-muted-foreground">({cards.length} pending)</span>
      </h2>
      <div className="flex flex-col gap-3">
        {cards.map((card) =>
          card.kind === "task_item_routing" ? (
            <TaskItemRoutingCard key={card.id} card={card} />
          ) : null,
        )}
      </div>
    </section>
  );
}
