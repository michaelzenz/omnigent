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
import { useResolveTaskItem, useUpdateTaskItem } from "@/hooks/useAgentTasks";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import type { DispatchPayload, TaskItemSummary, TaskWorkerLane } from "@/lib/agentTasksApi";
import {
  buildWorkerOptions,
  profileIdForItem,
  proposalHasEdits,
  workerOptionLabel,
  type WorkerOption,
} from "./taskCardUtils";

interface ItemEditorState {
  workerAgentId: string;
  model: string;
  title: string;
  description: string;
  instructions: string;
}

function itemProposalPayload(
  item: TaskItemSummary,
  workerLanes: TaskWorkerLane[],
): DispatchPayload & { description?: string } {
  return {
    worker_profile_id: profileIdForItem(item, workerLanes),
    title: item.title,
    description: item.description ?? "",
    instructions: item.instructions ?? "",
  };
}

function initialEditorState(
  item: TaskItemSummary,
  workerOptions: WorkerOption[],
  workerLanes: TaskWorkerLane[],
  defaultModel: string,
): ItemEditorState {
  const workerAgentId =
    profileIdForItem(item, workerLanes) ?? workerOptions[0]?.workerAgentId ?? "";
  const model =
    workerOptions.find((option) => option.workerAgentId === workerAgentId)?.model ??
    workerOptions[0]?.model ??
    defaultModel;
  return {
    workerAgentId,
    model,
    title: item.title,
    description: item.description ?? "",
    instructions: item.instructions ?? "",
  };
}

interface TaskCardItemEditorProps {
  taskId: string;
  item: TaskItemSummary;
  workerAgentIds: string[];
  workerLanes: TaskWorkerLane[];
  agents: AvailableAgent[];
  defaultModel: string;
  mode: "ack" | "edit";
}

export function TaskCardItemEditor({
  taskId,
  item,
  workerAgentIds,
  workerLanes,
  agents,
  defaultModel,
  mode,
}: TaskCardItemEditorProps) {
  const resolveItem = useResolveTaskItem(taskId);
  const updateItem = useUpdateTaskItem(taskId);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);

  const workerOptions = useMemo(
    () => buildWorkerOptions(workerAgentIds, itemProposalPayload(item, workerLanes), defaultModel),
    [workerAgentIds, workerLanes, item, defaultModel],
  );

  const agentNameById = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent.display_name])),
    [agents],
  );

  const [editor, setEditor] = useState(() =>
    initialEditorState(item, workerOptions, workerLanes, defaultModel),
  );

  useEffect(() => {
    setEditor(initialEditorState(item, workerOptions, workerLanes, defaultModel));
  }, [item.id, workerOptions, workerLanes, defaultModel]);

  useAutoGrowTextarea(instructionsRef, editor.instructions, 12, item.id);

  const baseline = {
    ...itemProposalPayload(item, workerLanes),
    model:
      workerOptions.find((option) => option.workerAgentId === editor.workerAgentId)?.model ??
      defaultModel,
  };
  const pending = resolveItem.isPending || updateItem.isPending;

  const onWorkerChange = (workerAgentId: string) => {
    const option = workerOptions.find((row) => row.workerAgentId === workerAgentId);
    setEditor((prev) => ({
      ...prev,
      workerAgentId,
      model: option?.model ?? prev.model,
    }));
  };

  const submitAck = async (resolution: "accept_item" | "edit_and_dispatch" | "reject_item") => {
    const edited =
      resolution === "edit_and_dispatch"
        ? ({
            worker_profile_id: editor.workerAgentId,
            model: editor.model,
            title: editor.title,
            description: editor.description,
            instructions: editor.instructions,
          } satisfies DispatchPayload & { description?: string })
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

  const saveEdit = async () => {
    await updateItem.mutateAsync({
      taskItemId: item.id,
      body: {
        worker_profile_id: editor.workerAgentId,
        title: editor.title,
        description: editor.description,
        instructions: editor.instructions,
      },
    });
  };

  const dirty = proposalHasEdits(baseline, editor);

  return (
    <div className="space-y-1.5">
      {editor.description ? (
        <p className="text-xs leading-snug whitespace-pre-wrap text-muted-foreground">
          {editor.description}
        </p>
      ) : null}

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
          className="field-sizing-fixed min-h-7 resize-none overflow-y-auto py-1"
        />
      </div>

      {mode === "ack" ? (
        <div className="flex justify-end gap-1.5 pt-0.5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={pending}
            onClick={() => void submitAck("reject_item")}
            aria-label="Dismiss inbox item"
          >
            <XIcon aria-hidden />
            Skip
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={pending}
            onClick={() => void submitAck("accept_item")}
            aria-label="Approve inbox item"
          >
            <CheckIcon aria-hidden />
            Go
          </Button>
        </div>
      ) : (
        <div className="flex justify-end pt-0.5">
          <Button
            type="button"
            size="sm"
            disabled={!dirty || pending}
            onClick={() => void saveEdit()}
          >
            Save
          </Button>
        </div>
      )}
    </div>
  );
}
