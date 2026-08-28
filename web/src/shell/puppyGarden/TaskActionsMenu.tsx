import { useState } from "react";
import { ArchiveIcon, Loader2Icon, MoreHorizontalIcon, Trash2Icon } from "lucide-react";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const isArchived = taskState === "archived";
  const pending = archiveTask.isPending || deleteTask.isPending;

  const handleArchive = async () => {
    try {
      await archiveTask.mutateAsync();
      setArchiveOpen(false);
      setDropdownOpen(false);
    } catch {
      // mutation error is handled by the hook
    }
  };

  const handleDelete = async () => {
    setDeleteError(null);
    try {
      await deleteTask.mutateAsync();
      setDeleteOpen(false);
      setDropdownOpen(false);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Failed to delete task");
    }
  };

  return (
    <>
      <DropdownMenu open={dropdownOpen} onOpenChange={setDropdownOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="shrink-0"
            disabled={pending}
            aria-label="More actions"
            onClick={(e) => e.stopPropagation()}
          >
            {pending ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <MoreHorizontalIcon className="size-4" />
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          onClick={(e) => e.stopPropagation()}
        >
          {!isArchived && (
            <DropdownMenuItem
              onClick={() => {
                setDropdownOpen(false);
                setArchiveOpen(true);
              }}
            >
              <ArchiveIcon className="size-4" />
              Archive
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onClick={() => {
              setDropdownOpen(false);
              setDeleteOpen(true);
            }}
          >
            <Trash2Icon className="size-4" />
            Delete permanently
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={archiveOpen} onOpenChange={setArchiveOpen}>
        <DialogContent onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle>Archive task?</DialogTitle>
            <DialogDescription>
              The task will be hidden from the board but its data is kept.
              You can still find it later by querying archived tasks.
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
              {archiveTask.isPending ? (
                <Loader2Icon className="mr-2 size-4 animate-spin" />
              ) : null}
              Archive
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle className="text-destructive">
              Delete task permanently?
            </DialogTitle>
            <DialogDescription>
              This action cannot be undone. All task data — items, workers,
              events, and assets — will be permanently removed.
              {!isArchived && (
                <span className="mt-2 block font-medium text-destructive">
                  The task must be archived first before it can be permanently deleted.
                </span>
              )}
            </DialogDescription>
          </DialogHeader>
          {deleteError ? (
            <p className="text-sm text-destructive">{deleteError}</p>
          ) : null}
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant="destructive"
              disabled={deleteTask.isPending || !isArchived}
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
