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
import { useResolveTaskItem, useUpdateTaskItem } from "@/hooks/useAgentTasks";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import { useRoleProfiles } from "@/hooks/useRoleProfiles";
import {
  WORKER_ROLE_PREFIX,
  type DispatchPayload,
  type TaskItemSummary,
  type TaskWorkerLane,
} from "@/lib/agentTasksApi";
import {
  buildWorkerOptions,
  proposalHasEdits,
  roleKeyForItem,
  workerOptionLabel,
  type WorkerOption,
} from "./taskCardUtils";

interface ItemEditorState {
  workerRoleKey: string;
  title: string;
  description: string;
  instructions: string;
}

function itemProposalPayload(
  item: TaskItemSummary,
  workerLanes: TaskWorkerLane[],
): DispatchPayload & { description?: string } {
  return {
    worker_role_key: roleKeyForItem(item, workerLanes),
    title: item.title,
    description: item.description ?? "",
    instructions: item.instructions ?? "",
  };
}

function initialEditorState(
  item: TaskItemSummary,
  workerOptions: WorkerOption[],
  laneRoleKey: string | undefined,
): ItemEditorState {
  const workerRoleKey = laneRoleKey ?? workerOptions[0]?.workerRoleKey ?? "";
  return {
    workerRoleKey,
    title: item.title,
    description: item.description ?? "",
    instructions: item.instructions ?? "",
  };
}

interface TaskCardItemEditorProps {
  taskId: string;
  item: TaskItemSummary;
  workerLanes: TaskWorkerLane[];
  mode: "ack" | "edit" | "parked";
}

export function TaskCardItemEditor({ taskId, item, workerLanes, mode }: TaskCardItemEditorProps) {
  const resolveItem = useResolveTaskItem(taskId);
  const updateItem = useUpdateTaskItem(taskId);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);
  const { data: workerRoles = [] } = useRoleProfiles(WORKER_ROLE_PREFIX);

  const roleKeys = workerRoles.map((role) => role.role).join(",");
  const laneRoleKey = roleKeyForItem(item, workerLanes);

  const workerOptions = useMemo(
    () =>
      buildWorkerOptions(roleKeys ? roleKeys.split(",") : [], {
        worker_role_key: laneRoleKey,
      }),
    [roleKeys, laneRoleKey],
  );

  const roleTitleByKey = useMemo(
    () => new Map(workerRoles.map((role) => [role.role, role.title ?? role.role])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [roleKeys],
  );

  const [editor, setEditor] = useState(() => initialEditorState(item, workerOptions, laneRoleKey));

  useEffect(() => {
    setEditor(initialEditorState(item, workerOptions, laneRoleKey));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.id, workerOptions, laneRoleKey]);

  useAutoGrowTextarea(instructionsRef, editor.instructions, 12, item.id);

  const baseline = itemProposalPayload(item, workerLanes);
  const pending = resolveItem.isPending || updateItem.isPending;

  const onWorkerChange = (workerRoleKey: string) => {
    setEditor((prev) => ({
      ...prev,
      workerRoleKey,
    }));
  };

  const submitAck = async (resolution: "accept_item" | "edit_and_dispatch" | "reject_item") => {
    const edited =
      resolution === "edit_and_dispatch"
        ? ({
            worker_role_key: editor.workerRoleKey,
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
        worker_role_key: editor.workerRoleKey,
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
        <Select value={editor.workerRoleKey} onValueChange={onWorkerChange}>
          <SelectTrigger className="h-7 w-full" size="sm">
            <SelectValue placeholder="Select worker role" />
          </SelectTrigger>
          <SelectContent>
            {workerOptions.map((option) => (
              <SelectItem key={option.workerRoleKey} value={option.workerRoleKey}>
                {workerOptionLabel(option.workerRoleKey, roleTitleByKey)}
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
          onChange={(event) => setEditor((prev) => ({ ...prev, instructions: event.target.value }))}
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
      ) : mode === "parked" ? (
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
