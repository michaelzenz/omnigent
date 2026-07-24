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
import { useResolveTaskItem } from "@/hooks/useAgentTasks";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import {
  type DispatchPayload,
  type TaskItemSummary,
} from "@/lib/agentTasksApi";
import {
  buildWorkerOptions,
  proposalHasEdits,
  workerOptionLabel,
  type WorkerOption,
} from "./taskCardUtils";

interface TaskCardInboxProps {
  taskId: string;
  inboxItems: TaskItemSummary[];
  workerGroups: { worker_agent_id: string }[];
  agents: AvailableAgent[];
  defaultModel: string;
}

interface InboxEditorState {
  workerAgentId: string;
  model: string;
  title: string;
  instructions: string;
}

function itemDispatchPayload(item: TaskItemSummary): DispatchPayload {
  return {
    worker_agent_id: item.worker_agent_id ?? undefined,
    model: item.model ?? undefined,
    title: item.title,
    instructions: item.instructions ?? "",
  };
}

function initialEditorState(
  item: TaskItemSummary,
  workerOptions: WorkerOption[],
): InboxEditorState {
  const workerAgentId =
    item.worker_agent_id ?? workerOptions[0]?.workerAgentId ?? "";
  const model =
    item.model ??
    workerOptions.find((option) => option.workerAgentId === workerAgentId)?.model ??
    workerOptions[0]?.model ??
    "";
  return {
    workerAgentId,
    model,
    title: item.title,
    instructions: item.instructions ?? "",
  };
}

interface InboxItemCardProps {
  taskId: string;
  item: TaskItemSummary;
  workerAgentIds: string[];
  agents: AvailableAgent[];
  defaultModel: string;
}

function InboxItemCard({
  taskId,
  item,
  workerAgentIds,
  agents,
  defaultModel,
}: InboxItemCardProps) {
  const resolveItem = useResolveTaskItem(taskId);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);

  const workerOptions = useMemo(
    () => buildWorkerOptions(workerAgentIds, itemDispatchPayload(item), defaultModel),
    [workerAgentIds, item, defaultModel],
  );

  const agentNameById = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent.display_name])),
    [agents],
  );

  const [editor, setEditor] = useState(() => initialEditorState(item, workerOptions));

  useEffect(() => {
    setEditor(initialEditorState(item, workerOptions));
  }, [item.id, workerOptions]);

  useAutoGrowTextarea(instructionsRef, editor.instructions, 12);

  const baseline = itemDispatchPayload(item);

  const onWorkerChange = (workerAgentId: string) => {
    const option = workerOptions.find((row) => row.workerAgentId === workerAgentId);
    setEditor((prev) => ({
      ...prev,
      workerAgentId,
      model: option?.model ?? prev.model,
    }));
  };

  const submit = async (resolution: "accept_item" | "edit_and_dispatch" | "reject_item") => {
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
      resolution === "accept_item" && proposalHasEdits(baseline, editor)
        ? "edit_and_dispatch"
        : resolution;

    await resolveItem.mutateAsync({
      taskItemId: item.id,
      resolution: effectiveResolution,
      edited_payload: effectiveResolution === "edit_and_dispatch" ? edited : undefined,
    });
  };

  return (
    <article
      className="rounded-md border border-border bg-background p-2 shadow-sm"
      data-testid={`inbox-item-${item.id}`}
    >
      <div className="space-y-1.5">
        <div className="flex flex-col gap-0.5">
          <span className="text-xs leading-none text-muted-foreground">Worker</span>
          <Select value={editor.workerAgentId} onValueChange={onWorkerChange}>
            <SelectTrigger className="h-7 w-full" size="sm">
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

        <div className="flex flex-col gap-0.5">
          <span className="text-xs leading-none text-muted-foreground">Title</span>
          <Input
            className="h-7 py-1"
            value={editor.title}
            onChange={(event) => setEditor((prev) => ({ ...prev, title: event.target.value }))}
          />
        </div>

        <div className="flex flex-col gap-0.5">
          <span className="text-xs leading-none text-muted-foreground">Instructions</span>
          <Textarea
            ref={instructionsRef}
            rows={1}
            value={editor.instructions}
            onChange={(event) =>
              setEditor((prev) => ({ ...prev, instructions: event.target.value }))
            }
            className="min-h-7 resize-none overflow-y-auto py-1"
          />
        </div>

        <div className="flex justify-end gap-1.5 pt-0.5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={resolveItem.isPending}
            onClick={() => void submit("reject_item")}
            aria-label="Dismiss inbox item"
          >
            <XIcon aria-hidden />
            Skip
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={resolveItem.isPending}
            onClick={() => void submit("accept_item")}
            aria-label="Approve inbox item"
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
  inboxItems,
  workerGroups,
  agents,
  defaultModel,
}: TaskCardInboxProps) {
  const workerAgentIds = useMemo(
    () => workerGroups.map((group) => group.worker_agent_id),
    [workerGroups],
  );

  return (
    <section className="flex max-h-72 min-h-0 flex-col border-b border-border bg-amber-50/60 px-3 py-2 dark:bg-amber-950/20">
      <h3 className="mb-1 shrink-0 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Inbox
        {inboxItems.length > 0 ? (
          <span className="ml-2 font-normal text-muted-foreground normal-case">
            ({inboxItems.length})
          </span>
        ) : null}
      </h3>

      {inboxItems.length === 0 ? (
        <p className="text-sm text-muted-foreground">No items awaiting approval.</p>
      ) : (
        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
          {inboxItems.map((item) => (
            <InboxItemCard
              key={item.id}
              taskId={taskId}
              item={item}
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
