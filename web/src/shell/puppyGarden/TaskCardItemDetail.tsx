import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { XIcon } from "lucide-react";
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
import { useUpdateTaskItem } from "@/hooks/useAgentTasks";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import type { TaskExecutionSummary, TaskItemSummary } from "@/lib/agentTasksApi";
import { relativeTime } from "@/lib/relativeTime";
import {
  buildWorkerOptions,
  isExecutionEditable,
  proposalHasEdits,
  workerOptionLabel,
  type WorkerOption,
} from "./taskCardUtils";
import { WorkStateBadge, executionSubtitle } from "./TaskCardSessions";

interface TaskCardItemDetailProps {
  taskId: string;
  execution: TaskExecutionSummary;
  workerAgentIds: string[];
  agents: AvailableAgent[];
  defaultModel: string;
  onClose: () => void;
}

interface ItemEditorState {
  workerAgentId: string;
  model: string;
  title: string;
  instructions: string;
}

function itemEditorState(item: TaskItemSummary, workerOptions: WorkerOption[]): ItemEditorState {
  const workerAgentId = item.worker_agent_id ?? workerOptions[0]?.workerAgentId ?? "";
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

export function TaskCardItemDetail({
  taskId,
  execution,
  workerAgentIds,
  agents,
  defaultModel,
  onClose,
}: TaskCardItemDetailProps) {
  const item = execution.item;
  const editable = isExecutionEditable(execution.status) && item != null;
  const updateItem = useUpdateTaskItem(taskId);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);

  const workerOptions = useMemo(
    () =>
      item
        ? buildWorkerOptions(
            workerAgentIds,
            {
              worker_agent_id: item.worker_agent_id ?? undefined,
              model: item.model ?? undefined,
              title: item.title,
              instructions: item.instructions ?? "",
            },
            defaultModel,
          )
        : [],
    [workerAgentIds, item, defaultModel],
  );

  const agentNameById = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent.display_name])),
    [agents],
  );

  const [editor, setEditor] = useState<ItemEditorState | null>(
    item ? itemEditorState(item, workerOptions) : null,
  );

  useEffect(() => {
    if (item) {
      setEditor(itemEditorState(item, workerOptions));
    }
  }, [item?.id, workerOptions]);

  useAutoGrowTextarea(instructionsRef, editor?.instructions ?? "", 12, execution.id);

  if (item == null || editor == null) {
    return (
      <div className="flex min-h-0 flex-1 flex-col p-2" data-testid="task-item-detail">
        <div className="mb-1 flex items-center justify-between gap-2">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Task item
          </h3>
          <Button type="button" variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close">
            <XIcon aria-hidden />
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">Task item details are unavailable.</p>
      </div>
    );
  }

  const baseline = {
    worker_agent_id: item.worker_agent_id ?? undefined,
    title: item.title,
    instructions: item.instructions ?? "",
    model: item.model ?? undefined,
  };

  const dirty = proposalHasEdits(baseline, editor);

  const onWorkerChange = (workerAgentId: string) => {
    const option = workerOptions.find((row) => row.workerAgentId === workerAgentId);
    setEditor((prev) =>
      prev
        ? {
            ...prev,
            workerAgentId,
            model: option?.model ?? prev.model,
          }
        : prev,
    );
  };

  const save = async () => {
    await updateItem.mutateAsync({
      taskItemId: item.id,
      body: {
        worker_agent_id: editor.workerAgentId,
        model: editor.model,
        title: editor.title,
        instructions: editor.instructions,
      },
    });
  };

  const subtitle = executionSubtitle(execution);
  const workerId = item.worker_agent_id ?? "";
  const workerName =
    agentNameById.get(workerId) ??
    agents.find((agent) => agent.id === workerId)?.name ??
    workerId;

  return (
    <div className="flex min-h-0 flex-1 flex-col p-2" data-testid="task-item-detail">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Task item
        </h3>
        <Button type="button" variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close">
          <XIcon aria-hidden />
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
        <div className="flex items-center justify-between gap-2">
          <p className="truncate text-xs text-muted-foreground">{workerName}</p>
          <WorkStateBadge status={execution.status} />
        </div>

        {editable ? (
          <>
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
                onChange={(event) =>
                  setEditor((prev) => (prev ? { ...prev, title: event.target.value } : prev))
                }
              />
            </div>

            <div className="flex flex-col gap-0.5">
              <span className="text-xs leading-none text-muted-foreground">Instructions</span>
              <Textarea
                ref={instructionsRef}
                rows={1}
                value={editor.instructions}
                onChange={(event) =>
                  setEditor((prev) =>
                    prev ? { ...prev, instructions: event.target.value } : prev,
                  )
                }
                className="field-sizing-fixed min-h-7 resize-none overflow-y-auto py-1"
              />
            </div>

            <div className="flex justify-end pt-0.5">
              <Button
                type="button"
                size="sm"
                disabled={!dirty || updateItem.isPending}
                onClick={() => void save()}
              >
                Save
              </Button>
            </div>
          </>
        ) : (
          <>
            <div>
              <p className="text-sm leading-tight font-medium">{item.title}</p>
              {item.instructions ? (
                <p className="mt-1 text-xs leading-snug whitespace-pre-wrap text-muted-foreground">
                  {item.instructions}
                </p>
              ) : null}
            </div>
            {subtitle ? <p className="text-xs text-muted-foreground">{subtitle}</p> : null}
            {execution.conversation_id ? (
              <Link
                to={`/c/${execution.conversation_id}`}
                className="inline-flex text-xs text-primary hover:underline"
              >
                Open worker session
              </Link>
            ) : null}
            <dl className="space-y-1 text-xs text-muted-foreground">
              <div className="flex justify-between gap-2">
                <dt>Assigned</dt>
                <dd>{relativeTime(execution.assigned_at * 1000)}</dd>
              </div>
              {execution.started_at != null ? (
                <div className="flex justify-between gap-2">
                  <dt>Started</dt>
                  <dd>{relativeTime(execution.started_at * 1000)}</dd>
                </div>
              ) : null}
              {execution.finished_at != null ? (
                <div className="flex justify-between gap-2">
                  <dt>Finished</dt>
                  <dd>{relativeTime(execution.finished_at * 1000)}</dd>
                </div>
              ) : null}
            </dl>
          </>
        )}
      </div>
    </div>
  );
}
