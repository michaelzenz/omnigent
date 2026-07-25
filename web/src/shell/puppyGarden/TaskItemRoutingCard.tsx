import { useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { BoardDecisionCard } from "@/lib/agentTasksApi";
import { useResolveRoutingProposal } from "@/hooks/useBoardDecisions";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";

interface TaskItemRoutingCardProps {
  card: BoardDecisionCard;
}

export function TaskItemRoutingCard({ card }: TaskItemRoutingCardProps) {
  const resolveRouting = useResolveRoutingProposal();
  const [selectedTaskId, setSelectedTaskId] = useState(
    card.body.suggested_task_id ?? card.body.proposed_task?.task_id ?? "",
  );
  const [instructions, setInstructions] = useState(card.body.instructions ?? "");
  const [proposedTaskTitle, setProposedTaskTitle] = useState(
    card.body.proposed_task?.title ?? "",
  );
  const [proposedTaskCharter, setProposedTaskCharter] = useState(
    card.body.proposed_task?.charter ?? "",
  );
  const [showEvents, setShowEvents] = useState(false);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);

  useAutoGrowTextarea(instructionsRef, instructions, 12, card.id);

  const selectedCandidate = card.body.candidates.find(
    (candidate) => candidate.task_id === selectedTaskId,
  );
  const isNewTaskSelected = selectedCandidate?.is_new === true;

  const signalLabel = useMemo(() => {
    const count = card.body.events.length;
    return count === 1 ? "1 signal" : `${count} signals`;
  }, [card.body.events.length]);

  const workerLine = useMemo(() => {
    const parts = [
      card.body.worker_agent_id ? "worker assigned" : null,
      card.body.model,
    ].filter(Boolean);
    return parts.length > 0 ? parts.join(" · ") : null;
  }, [card.body.model, card.body.worker_agent_id]);

  const onAccept = async () => {
    const baseline = card.body.instructions ?? "";
    const baselineTitle = card.body.proposed_task?.title ?? "";
    const baselineCharter = card.body.proposed_task?.charter ?? "";
    await resolveRouting.mutateAsync({
      itemId: card.id,
      resolution: "accept_routing",
      selectedTaskId,
      instructions: instructions !== baseline ? instructions : undefined,
      proposedTaskTitle:
        isNewTaskSelected && proposedTaskTitle !== baselineTitle
          ? proposedTaskTitle
          : isNewTaskSelected
            ? proposedTaskTitle
            : undefined,
      proposedTaskCharter:
        isNewTaskSelected && proposedTaskCharter !== baselineCharter
          ? proposedTaskCharter
          : isNewTaskSelected
            ? proposedTaskCharter
            : undefined,
    });
  };

  const onReject = async () => {
    await resolveRouting.mutateAsync({
      itemId: card.id,
      resolution: "reject_routing",
    });
  };

  return (
    <article
      className="rounded-md border border-border bg-background p-3 shadow-sm"
      data-testid={`routing-card-${card.id}`}
    >
      <header className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Route work item
          </p>
          <h3 className="truncate text-sm font-semibold leading-tight">{card.body.title}</h3>
          <p className="text-xs text-muted-foreground">{signalLabel}</p>
        </div>
      </header>

      <div className="mb-2 flex flex-col gap-0.5">
        <span className="text-xs leading-none text-muted-foreground">Instructions</span>
        <Textarea
          ref={instructionsRef}
          rows={1}
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
          className="field-sizing-fixed min-h-7 resize-none overflow-y-auto py-1 text-sm"
        />
      </div>

      <div className="mb-2">
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

      <div className="mb-2 space-y-1">
        <p className="text-xs text-muted-foreground">Route to task</p>
        {card.body.candidates.map((candidate) => (
          <label
            key={candidate.task_id}
            className={cn(
              "flex cursor-pointer items-center justify-between gap-2 rounded-md border px-2 py-1.5 text-sm",
              selectedTaskId === candidate.task_id
                ? "border-ring bg-muted/40"
                : "border-border/60",
            )}
          >
            <span className="flex min-w-0 items-center gap-2">
              <input
                type="radio"
                name={`routing-task-${card.id}`}
                value={candidate.task_id}
                checked={selectedTaskId === candidate.task_id}
                onChange={() => setSelectedTaskId(candidate.task_id)}
                className="size-3.5"
              />
              <span className="truncate">
                {candidate.is_new ? `Create new task: ${candidate.task_title}` : candidate.task_title}
              </span>
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {candidate.is_new
                ? "new"
                : candidate.score != null
                  ? candidate.score.toFixed(2)
                  : ""}
              {candidate.recommended ? " ★" : ""}
            </span>
          </label>
        ))}
      </div>

      {isNewTaskSelected ? (
        <div className="mb-2 space-y-1.5">
          <div className="flex flex-col gap-0.5">
            <span className="text-xs leading-none text-muted-foreground">New task title</span>
            <Input
              className="h-7 py-1"
              value={proposedTaskTitle}
              onChange={(event) => setProposedTaskTitle(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-xs leading-none text-muted-foreground">Charter</span>
            <Textarea
              rows={2}
              value={proposedTaskCharter}
              onChange={(event) => setProposedTaskCharter(event.target.value)}
              className="min-h-14 resize-y py-1 text-sm"
            />
          </div>
        </div>
      ) : null}

      {workerLine ? (
        <p className="mb-2 text-xs text-muted-foreground">{workerLine}</p>
      ) : null}

      {card.rationale ? (
        <p className="mb-3 line-clamp-2 text-xs text-muted-foreground">{card.rationale}</p>
      ) : null}

      <footer className="flex justify-end gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={resolveRouting.isPending}
          onClick={() => void onReject()}
        >
          Reject
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={resolveRouting.isPending || !selectedTaskId}
          onClick={() => void onAccept()}
        >
          Accept & run
        </Button>
      </footer>
    </article>
  );
}
