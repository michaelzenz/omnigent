import { Loader2Icon, RotateCcwIcon, SquareIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRemoveTaskItem, useRetryTaskItem, useStopTaskItem } from "@/hooks/useAgentTasks";
import type { TaskItemSummary } from "@/lib/agentTasksApi";
import { isParkedItemState } from "./taskCardUtils";

interface TaskCardRowActionsProps {
  taskId: string;
  item: TaskItemSummary;
  conversationId?: string | null;
  showStop?: boolean;
}

export function TaskCardRowActions({
  taskId,
  item,
  conversationId,
  showStop = false,
}: TaskCardRowActionsProps) {
  const stopItem = useStopTaskItem(taskId);
  const removeItem = useRemoveTaskItem(taskId);
  const retryItem = useRetryTaskItem(taskId);
  const pending = stopItem.isPending || removeItem.isPending || retryItem.isPending;

  if (showStop) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={pending}
        onClick={() =>
          void stopItem.mutateAsync({
            taskItemId: item.id,
            queueItemId: item.queue_item_id,
            conversationId,
          })
        }
        aria-label="Stop running work"
      >
        {stopItem.isPending ? (
          <Loader2Icon className="size-3.5 animate-spin" aria-hidden />
        ) : (
          <SquareIcon className="size-3.5" aria-hidden />
        )}
        Stop
      </Button>
    );
  }

  if (!isParkedItemState(item.state)) {
    return null;
  }

  return (
    <div className="flex justify-end gap-1.5 pt-0.5">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={pending}
        onClick={() =>
          void removeItem.mutateAsync({
            taskItemId: item.id,
            queueItemId: item.queue_item_id,
          })
        }
        aria-label="Remove item from queue"
      >
        <XIcon className="size-3.5" aria-hidden />
        Remove
      </Button>
      <Button
        type="button"
        size="sm"
        disabled={pending}
        onClick={() => void retryItem.mutateAsync(item.id)}
        aria-label="Retry dispatch"
      >
        {retryItem.isPending ? (
          <Loader2Icon className="size-3.5 animate-spin" aria-hidden />
        ) : (
          <RotateCcwIcon className="size-3.5" aria-hidden />
        )}
        Retry
      </Button>
    </div>
  );
}

interface TaskCardExecutionRowActionsProps {
  taskId: string;
  taskItemId: string;
  queueItemId?: string | null;
  conversationId?: string | null;
}

export function TaskCardExecutionRowActions({
  taskId,
  taskItemId,
  queueItemId,
  conversationId,
}: TaskCardExecutionRowActionsProps) {
  const stopItem = useStopTaskItem(taskId);

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={stopItem.isPending}
      onClick={() =>
        void stopItem.mutateAsync({
          taskItemId,
          queueItemId,
          conversationId,
        })
      }
      aria-label="Stop running work"
    >
      {stopItem.isPending ? (
        <Loader2Icon className="size-3.5 animate-spin" aria-hidden />
      ) : (
        <SquareIcon className="size-3.5" aria-hidden />
      )}
      Stop
    </Button>
  );
}
