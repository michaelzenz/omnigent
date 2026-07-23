import { useEffect, useMemo, useRef, useState } from "react";
import { CheckIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import { useResolveTaskProposal } from "@/hooks/useAgentTasks";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import {
  parseEventPayload,
  type DispatchPayload,
  type TaskEventSummary,
} from "@/lib/agentTasksApi";
import {
  buildWorkerOptions,
  proposalHasEdits,
  workerOptionLabel,
  type WorkerOption,
} from "./taskCardUtils";

interface TaskCardInboxProps {
  taskId: string;
  proposals: TaskEventSummary[];
  workerGroups: { worker_agent_id: string }[];
  agents: AvailableAgent[];
  defaultModel: string;
}

interface ProposalEditorState {
  workerAgentId: string;
  model: string;
  title: string;
  instructions: string;
}

function initialEditorState(
  proposal: TaskEventSummary,
  workerOptions: WorkerOption[],
): ProposalEditorState {
  const payload = parseEventPayload(proposal.payload);
  const workerAgentId =
    payload.worker_agent_id ?? workerOptions[0]?.workerAgentId ?? "";
  const model =
    payload.model ??
    workerOptions.find((option) => option.workerAgentId === workerAgentId)?.model ??
    workerOptions[0]?.model ??
    "";
  return {
    workerAgentId,
    model,
    title: payload.title ?? proposal.title,
    instructions: payload.instructions ?? "",
  };
}

interface ProposalInboxCardProps {
  taskId: string;
  proposal: TaskEventSummary;
  workerAgentIds: string[];
  agents: AvailableAgent[];
  defaultModel: string;
}

function ProposalInboxCard({
  taskId,
  proposal,
  workerAgentIds,
  agents,
  defaultModel,
}: ProposalInboxCardProps) {
  const resolveProposal = useResolveTaskProposal(taskId);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);

  const workerOptions = useMemo(
    () => buildWorkerOptions(workerAgentIds, parseEventPayload(proposal.payload), defaultModel),
    [workerAgentIds, proposal.payload, defaultModel],
  );

  const agentNameById = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent.display_name])),
    [agents],
  );

  const [editor, setEditor] = useState(() => initialEditorState(proposal, workerOptions));

  useEffect(() => {
    setEditor(initialEditorState(proposal, workerOptions));
  }, [proposal.id, workerOptions]);

  useAutoGrowTextarea(instructionsRef, editor.instructions, 12);

  const baseline = parseEventPayload(proposal.payload);

  const onWorkerChange = (workerAgentId: string) => {
    const option = workerOptions.find((row) => row.workerAgentId === workerAgentId);
    setEditor((prev) => ({
      ...prev,
      workerAgentId,
      model: option?.model ?? prev.model,
    }));
  };

  const submit = async (resolution: "accept_proposal" | "edit_and_dispatch" | "reject_proposal") => {
    const edited =
      resolution === "edit_and_dispatch"
        ? ({
            worker_agent_id: editor.workerAgentId,
            model: editor.model,
            title: editor.title,
            instructions: editor.instructions,
          } satisfies DispatchPayload)
        : undefined;

    const effectiveResolution =
      resolution === "accept_proposal" && proposalHasEdits(baseline, editor)
        ? "edit_and_dispatch"
        : resolution;

    await resolveProposal.mutateAsync({
      eventId: proposal.id,
      resolution: effectiveResolution,
      edited_payload: effectiveResolution === "edit_and_dispatch" ? edited : undefined,
    });
  };

  return (
    <article
      className="rounded-md border border-border bg-background p-3 shadow-sm"
      data-testid={`inbox-proposal-${proposal.id}`}
    >
      <div className="space-y-3">
        <div className="space-y-1">
          <span className="text-xs text-muted-foreground">Worker</span>
          <Select value={editor.workerAgentId} onValueChange={onWorkerChange}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select worker" />
            </SelectTrigger>
            <SelectContent>
              {workerOptions.map((option) => (
                <SelectItem key={option.workerAgentId} value={option.workerAgentId}>
                  {workerOptionLabel(option.workerAgentId, option.model, agentNameById)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <span className="text-xs text-muted-foreground">Title</span>
          <Input
            value={editor.title}
            onChange={(event) => setEditor((prev) => ({ ...prev, title: event.target.value }))}
          />
        </div>

        <div className="space-y-1">
          <span className="text-xs text-muted-foreground">Instructions</span>
          <Textarea
            ref={instructionsRef}
            rows={1}
            value={editor.instructions}
            onChange={(event) =>
              setEditor((prev) => ({ ...prev, instructions: event.target.value }))
            }
            className="min-h-8 resize-none overflow-y-auto py-1.5"
          />
        </div>

        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={resolveProposal.isPending}
            onClick={() => void submit("reject_proposal")}
            aria-label="Dismiss proposal"
          >
            <XIcon aria-hidden />
            Skip
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={resolveProposal.isPending}
            onClick={() => void submit("accept_proposal")}
            aria-label="Approve proposal"
          >
            <CheckIcon aria-hidden />
            Go
          </Button>
        </div>
      </div>
    </article>
  );
}

export function TaskCardInbox({
  taskId,
  proposals,
  workerGroups,
  agents,
  defaultModel,
}: TaskCardInboxProps) {
  const workerAgentIds = useMemo(
    () => workerGroups.map((group) => group.worker_agent_id),
    [workerGroups],
  );

  return (
    <section className="flex max-h-72 min-h-0 flex-col border-b border-border bg-amber-50/60 px-4 py-3 dark:bg-amber-950/20">
      <h3 className="mb-2 shrink-0 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Inbox
        {proposals.length > 0 ? (
          <span className="ml-2 font-normal text-muted-foreground normal-case">
            ({proposals.length})
          </span>
        ) : null}
      </h3>

      {proposals.length === 0 ? (
        <p className="text-sm text-muted-foreground">No proposals awaiting approval.</p>
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {proposals.map((proposal) => (
            <ProposalInboxCard
              key={proposal.id}
              taskId={taskId}
              proposal={proposal}
              workerAgentIds={workerAgentIds}
              agents={agents}
              defaultModel={defaultModel}
            />
          ))}
        </div>
      )}
    </section>
  );
}
