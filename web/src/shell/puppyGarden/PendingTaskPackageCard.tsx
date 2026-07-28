import { Button } from "@/components/ui/button";
import type { PendingTaskPackageCard } from "@/lib/agentTasksApi";
import { useAcceptTaskPackage, useRejectTaskPackage } from "@/hooks/useBoardTriage";

interface PendingTaskPackageCardProps {
  card: PendingTaskPackageCard;
}

export function PendingTaskPackageCardView({ card }: PendingTaskPackageCardProps) {
  const acceptPackage = useAcceptTaskPackage();
  const rejectPackage = useRejectTaskPackage();
  const { task, inbox_items: inboxItems } = card.body;

  return (
    <article
      className="rounded-md border border-border bg-background p-3 shadow-sm"
      data-testid={`pending-package-${card.id}`}
    >
      <header className="mb-2">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          New task package
        </p>
        <h3 className="text-sm font-semibold leading-tight">{task.title}</h3>
        {task.description ? (
          <p className="mt-1 text-xs text-muted-foreground">{task.description}</p>
        ) : null}
        {task.charter ? (
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{task.charter}</p>
        ) : null}
      </header>

      {task.tags.length > 0 ? (
        <ul className="mb-2 flex flex-wrap gap-1">
          {task.tags.map((tag) => (
            <li
              key={`${tag.tag_type}:${tag.tag}`}
              className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
            >
              {tag.tag_type}:{tag.tag}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mb-3 space-y-1">
        <p className="text-xs text-muted-foreground">Inbox items</p>
        <ul className="space-y-1 text-sm">
          {inboxItems.map((item) => (
            <li key={item.id} className="rounded border border-border/60 px-2 py-1.5">
              <p className="font-medium">{item.title}</p>
              {item.instructions ? (
                <p className="text-xs text-muted-foreground">{item.instructions}</p>
              ) : null}
            </li>
          ))}
        </ul>
      </div>

      <footer className="flex justify-end gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={rejectPackage.isPending || acceptPackage.isPending}
          onClick={() => void rejectPackage.mutateAsync(card.id)}
        >
          Reject
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={rejectPackage.isPending || acceptPackage.isPending}
          onClick={() => void acceptPackage.mutateAsync(card.id)}
        >
          Accept
        </Button>
      </footer>
    </article>
  );
}
