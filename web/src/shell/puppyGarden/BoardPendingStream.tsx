import { Loader2Icon } from "lucide-react";
import { useBoardTriage } from "@/hooks/useBoardTriage";
import { PendingTaskPackageCardView } from "./PendingTaskPackageCard";

export function BoardPendingStream() {
  const { data: triage, isLoading, error } = useBoardTriage();
  const cards = triage?.pending ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" aria-hidden />
        Loading pending packages…
      </div>
    );
  }

  if (error) {
    return <p className="text-sm text-destructive">Failed to load pending packages.</p>;
  }

  if (!cards.length) {
    return null;
  }

  return (
    <section className="space-y-3" data-testid="board-pending-stream">
      <h2 className="text-sm font-semibold text-foreground">
        Pending
        <span className="ml-2 font-medium text-foreground/65">
          ({cards.length} package{cards.length === 1 ? "" : "s"})
        </span>
      </h2>
      <div className="flex flex-col gap-3">
        {cards.map((card) => (
          <PendingTaskPackageCardView key={card.id} card={card} />
        ))}
      </div>
    </section>
  );
}
