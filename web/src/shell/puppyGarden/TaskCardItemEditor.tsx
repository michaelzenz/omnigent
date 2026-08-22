import { useEffect, useRef, useState } from "react";
import { CheckIcon, CopyIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useResolveTaskItem, useUpdateTaskItem } from "@/hooks/useAgentTasks";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import type { TaskItemSummary, TaskWorkerLane } from "@/lib/agentTasksApi";

interface ItemEditorState {
  title: string;
  description: string;
  instructions: string;
}

function initialState(item: TaskItemSummary): ItemEditorState {
  return {
    title: item.title,
    description: item.description ?? "",
    instructions: item.instructions ?? "",
  };
}

interface TaskCardItemEditorProps {
  taskId: string;
  item: TaskItemSummary;
  workerLanes: TaskWorkerLane[];
  workerKind: string;
  mode: "ack" | "edit" | "parked";
}

export function TaskCardItemEditor({
  taskId,
  item,
  workerLanes,
  workerKind,
  mode,
}: TaskCardItemEditorProps) {
  const resolveItem = useResolveTaskItem(taskId);
  const updateItem = useUpdateTaskItem(taskId);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);
  const [editor, setEditor] = useState(() => initialState(item));

  useEffect(() => setEditor(initialState(item)), [item]);
  useAutoGrowTextarea(instructionsRef, editor.instructions, 12);

  const worker = workerLanes.find((lane) => lane.worker_id === item.worker_id);
  const pending = resolveItem.isPending || updateItem.isPending;
  const dirty =
    editor.title !== item.title ||
    editor.description !== (item.description ?? "") ||
    editor.instructions !== (item.instructions ?? "");

  const submitAck = async (resolution: "accept_item" | "edit_and_dispatch" | "reject_item") => {
    const effectiveResolution =
      resolution === "accept_item" && dirty ? "edit_and_dispatch" : resolution;
    await resolveItem.mutateAsync({
      taskItemId: item.id,
      resolution: effectiveResolution,
      edited_payload:
        effectiveResolution === "edit_and_dispatch"
          ? {
              title: editor.title,
              description: editor.description,
              instructions: editor.instructions,
            }
          : undefined,
    });
  };

  const saveEdit = async () => {
    await updateItem.mutateAsync({
      taskItemId: item.id,
      body: {
        title: editor.title,
        description: editor.description,
        instructions: editor.instructions,
      },
    });
  };

  return (
    <div className="space-y-1.5">
      {editor.description ? (
        <p className="whitespace-pre-wrap text-xs leading-snug text-muted-foreground">
          {editor.description}
        </p>
      ) : null}
      <p className="text-xs text-muted-foreground">
        Worker: {worker?.provider_name ?? (item.worker_id ? "Assigned" : "Unassigned")}
      </p>
      <div className="flex flex-col gap-0.5">
        <span className="text-xs leading-none text-muted-foreground">Title</span>
        <Input
          className="h-7 py-1"
          value={editor.title}
          onChange={(event) => setEditor((current) => ({ ...current, title: event.target.value }))}
        />
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="text-xs leading-none text-muted-foreground">Instructions</span>
        <Textarea
          ref={instructionsRef}
          rows={1}
          value={editor.instructions}
          onChange={(event) =>
            setEditor((current) => ({ ...current, instructions: event.target.value }))
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
            <XIcon aria-hidden /> Skip
          </Button>
          {workerKind === "external" ? (
            <Button
              type="button"
              size="sm"
              disabled={pending}
              onClick={async () => {
                if (editor.instructions) await navigator.clipboard.writeText(editor.instructions);
                void submitAck("accept_item");
              }}
            >
              <CopyIcon aria-hidden /> Copy
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              disabled={pending || item.worker_id == null}
              onClick={() => void submitAck("accept_item")}
            >
              <CheckIcon aria-hidden /> Accept
            </Button>
          )}
        </div>
      ) : mode === "edit" ? (
        <div className="flex justify-end pt-0.5">
          <Button
            type="button"
            size="sm"
            disabled={pending || !dirty}
            onClick={() => void saveEdit()}
          >
            <CheckIcon aria-hidden /> Save
          </Button>
        </div>
      ) : null}
    </div>
  );
}
