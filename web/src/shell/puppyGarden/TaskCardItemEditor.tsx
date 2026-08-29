import { useEffect, useRef, useState } from "react";
import { CheckIcon, CopyIcon, PencilIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useResolveTaskItem, useUpdateTaskItem } from "@/hooks/useAgentTasks";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import {
  acquireTaskItemEditLease,
  releaseTaskItemEditLease,
  type TaskItemSummary,
  type TaskWorkerLane,
} from "@/lib/agentTasksApi";
import { isPuppyGardenFixtureMode } from "./fixtures/puppyGardenFixtureMode";

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
  const [editing, setEditing] = useState(mode !== "edit");
  const [editLeaseToken, setEditLeaseToken] = useState<string | null>(null);
  const [leaseError, setLeaseError] = useState<string | null>(null);

  useEffect(() => setEditor(initialState(item)), [item]);
  useEffect(() => {
    if (!editLeaseToken || isPuppyGardenFixtureMode()) return;
    const heartbeat = window.setInterval(() => {
      void acquireTaskItemEditLease(item.id, editLeaseToken).catch(() => {
        setEditLeaseToken(null);
        setEditing(false);
        setLeaseError("The edit lease expired because the item started dispatching.");
      });
    }, 45_000);
    return () => {
      window.clearInterval(heartbeat);
      void releaseTaskItemEditLease(item.id, editLeaseToken).catch(() => undefined);
    };
  }, [editLeaseToken, item.id]);
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

  const releaseLease = async () => {
    const token = editLeaseToken;
    setEditLeaseToken(null);
    if (token && !isPuppyGardenFixtureMode()) {
      await releaseTaskItemEditLease(item.id, token).catch(() => undefined);
    }
  };

  const beginEdit = async () => {
    setLeaseError(null);
    if (isPuppyGardenFixtureMode()) {
      setEditLeaseToken("fixture");
      setEditing(true);
      return;
    }
    try {
      const lease = await acquireTaskItemEditLease(item.id);
      setEditLeaseToken(lease.token);
      setEditing(true);
    } catch (error) {
      setLeaseError(error instanceof Error ? error.message : "Item started dispatching");
    }
  };

  const saveEdit = async () => {
    await updateItem.mutateAsync({
      taskItemId: item.id,
      body: {
        title: editor.title,
        description: editor.description,
        instructions: editor.instructions,
        edit_lease_token: editLeaseToken ?? undefined,
      },
    });
    await releaseLease();
    setEditing(false);
  };

  if (mode === "edit" && !editing) {
    return (
      <div className="space-y-2">
        {item.instructions ? (
          <p className="whitespace-pre-wrap text-xs text-muted-foreground">{item.instructions}</p>
        ) : (
          <p className="text-xs text-muted-foreground">No instructions.</p>
        )}
        <Button type="button" size="sm" variant="outline" onClick={() => void beginEdit()}>
          <PencilIcon aria-hidden /> Edit instructions
        </Button>
        {leaseError ? <p className="text-xs text-destructive">{leaseError}</p> : null}
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      {editor.description ? (
        <p className="whitespace-pre-wrap text-xs leading-snug text-muted-foreground">
          {editor.description}
        </p>
      ) : null}
      <p className="text-xs text-muted-foreground">
        Worker: {worker?.provider_name ?? (item.worker_id ? "Assigned" : "Unassigned")}
        {worker?.workspace ? ` · ${worker.workspace}` : ""}
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
            title="Future turn-finished events from this session will be filtered out. You can still interact with the session directly."
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
      ) : mode === "edit" || mode === "parked" ? (
        <div className="flex justify-end gap-1.5 pt-0.5">
          {mode === "edit" ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => {
                setEditor(initialState(item));
                setEditing(false);
                void releaseLease();
              }}
            >
              Cancel
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            disabled={pending || !dirty || (mode === "edit" && !editLeaseToken)}
            onClick={() => void saveEdit()}
          >
            <CheckIcon aria-hidden /> Save
          </Button>
        </div>
      ) : null}
    </div>
  );
}
