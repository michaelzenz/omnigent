import { Badge } from "@/components/ui/badge";
import { Loader2Icon } from "lucide-react";
import { itemStateLabel, isParkedItemState } from "./taskCardUtils";

interface TaskCardItemStateBadgeProps {
  state: string;
}

export function TaskCardItemStateBadge({ state }: TaskCardItemStateBadgeProps) {
  const label = itemStateLabel(state);
  const variant =
    state === "running"
      ? "default"
      : state === "queued" || state === "pending"
        ? "secondary"
        : state === "dispatch_failed"
          ? "destructive"
          : isParkedItemState(state)
            ? "outline"
            : "outline";

  return (
    <Badge variant={variant} className="shrink-0 text-[10px]">
      {state === "running" ? (
        <span className="inline-flex items-center gap-1">
          <Loader2Icon className="size-3 animate-spin" aria-hidden />
          Running
        </span>
      ) : (
        label
      )}
    </Badge>
  );
}
