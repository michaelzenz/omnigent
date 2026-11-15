import { Button } from "@/components/ui/button";
import { useRequestManagers } from "@/hooks/useGlobalHubTasks";

/**
 * Says how many live tasks have no manager, with a button that asks the
 * broker to give each one a manager.
 *
 * @param count - The live tasks on the whole board without a manager.
 */
export function UnmanagedTasksLine({ count }: { count: number }) {
  const request = useRequestManagers();
  const one = count === 1;
  return (
    <section
      className="flex flex-wrap items-center gap-3 rounded-xl border-2 border-dashed border-destructive/40 bg-destructive/5 px-4 py-3"
      data-testid="globalhub-unmanaged"
    >
      <p className="text-sm font-medium">
        {one ? "1 task has no manager." : `${count.toLocaleString()} tasks have no manager.`}
      </p>
      <Button
        size="sm"
        variant="outline"
        loading={request.isPending}
        disabled={request.isSuccess}
        onClick={() => request.mutate()}
      >
        {one ? "Ask the broker to assign it" : "Ask the broker to assign them"}
      </Button>
      {request.data && (
        <p role="status" className="text-xs text-muted-foreground">
          {request.data.queued ? "Sent to the broker." : "Every task has a manager now."}
        </p>
      )}
      {request.error && (
        <p role="alert" className="w-full text-xs text-destructive">
          {request.error.message}
        </p>
      )}
    </section>
  );
}
