import { useState } from "react";
import { ArchiveIcon, Loader2Icon, Trash2Icon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useArchiveAgentTask, usePermanentlyDeleteAgentTask } from "@/hooks/useAgentTasks";

interface TaskActionsMenuProps {
  taskId: string;
  taskState: string;
}

export function TaskActionsMenu({ taskId, taskState }: TaskActionsMenuProps) {
  const archiveTask = useArchiveAgentTask(taskId);
  const deleteTask = usePermanentlyDeleteAgentTask(taskId);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const isArchived = taskState === "archived";
  const pending = archiveTask.isPending || deleteTask.isPending;

  const handleArchive = async () => {
    try {
      await archiveTask.mutateAsync();
      setArchiveOpen(false);
    } catch {
      // mutation error is handled by the hook
    }
  };

  const handleDelete = async () => {
    setDeleteError(null);
    try {
      await deleteTask.mutateAsync();
      setDeleteOpen(false);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Failed to delete task");
    }
  };

  return (
    <>
      {!isArchived && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          disabled={pending}
          aria-label="Archive task"
          title="Archive task"
          onClick={(e) => {
            e.stopPropagation();
            setArchiveOpen(true);
          }}
        >
          {archiveTask.isPending ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <ArchiveIcon className="size-4" />
          )}
        </Button>
      )}
      {/* "!" beats ghost's hover:bg-muted in the CSS cascade. */}
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="shrink-0 hover:bg-destructive/10!"
        disabled={pending}
        aria-label="Delete task permanently"
        title="Delete task permanently"
        onClick={(e) => {
          e.stopPropagation();
          setDeleteOpen(true);
        }}
      >
        {deleteTask.isPending ? (
          <Loader2Icon className="size-4 animate-spin" />
        ) : (
          <XIcon className="size-4 text-destructive" />
        )}
      </Button>

      <Dialog open={archiveOpen} onOpenChange={setArchiveOpen}>
        <DialogContent onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle>Archive task?</DialogTitle>
            <DialogDescription>
              The task will be hidden from the board but its data is kept. You can still find it
              later by querying archived tasks.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant="outline"
              disabled={archiveTask.isPending}
              onClick={() => void handleArchive()}
            >
              {archiveTask.isPending ? <Loader2Icon className="mr-2 size-4 animate-spin" /> : null}
              Archive
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle className="text-destructive">Delete task permanently?</DialogTitle>
            <DialogDescription>
              This action cannot be undone. All task data — items and events — will be permanently
              removed. Worker sessions and assets will be untracked but remain accessible as regular
              conversations.
            </DialogDescription>
          </DialogHeader>
          {deleteError ? <p className="text-sm text-destructive">{deleteError}</p> : null}
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant="destructive"
              disabled={deleteTask.isPending}
              onClick={() => void handleDelete()}
            >
              {deleteTask.isPending ? (
                <Loader2Icon className="mr-2 size-4 animate-spin" />
              ) : (
                <Trash2Icon className="mr-2 size-4" />
              )}
              Delete permanently
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
