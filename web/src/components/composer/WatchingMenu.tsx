import { EyeIcon, XIcon } from "lucide-react";
import { toast } from "sonner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  type SessionSubscription,
  useSessionSubscriptions,
  useUnsubscribeSession,
} from "@/hooks/useSessionSubscriptions";
import { cn } from "@/lib/utils";

/**
 * The sources that the session follows, e.g. the pull requests its agent opened. It shows
 * when the session follows at least one; the user can stop following each one.
 */
export function WatchingMenu({
  conversationId,
  className,
}: {
  conversationId: string | null;
  className?: string;
}) {
  const subscriptions = useSessionSubscriptions(conversationId);
  if (conversationId === null || !subscriptions.data || subscriptions.data.length === 0) {
    return null;
  }
  return (
    <WatchingButton
      conversationId={conversationId}
      subscriptions={subscriptions.data}
      className={className}
    />
  );
}

function WatchingButton({
  conversationId,
  subscriptions,
  className,
}: {
  conversationId: string;
  subscriptions: SessionSubscription[];
  className?: string;
}) {
  const unsubscribe = useUnsubscribeSession(conversationId);
  const stopFollowing = (subscription: SessionSubscription) =>
    unsubscribe.mutate(subscription.source_key, {
      onError: (error) => toast.error("Could not stop watching", { description: error.message }),
    });
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-testid="composer-watching"
          title="Sources this session follows"
          className={cn(
            "flex shrink-0 items-center gap-1 rounded text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
            className,
          )}
        >
          <EyeIcon className="size-3.5 shrink-0" aria-hidden />
          <span>Watching {subscriptions.length}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="end" className="min-w-56 max-w-96">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          Changes to these sources come to this session.
        </DropdownMenuLabel>
        {subscriptions.map((subscription) => (
          <DropdownMenuItem
            key={subscription.id}
            title={subscription.note ?? subscription.source_key}
            onSelect={(event) => event.preventDefault()}
            className="flex items-center gap-2"
            data-testid={`composer-watching-${subscription.id}`}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate">{subscription.source_key}</span>
              <span className="block truncate font-mono text-xs text-muted-foreground">
                {subscription.source_type}
              </span>
            </span>
            <button
              type="button"
              aria-label={`Stop watching ${subscription.source_key}`}
              disabled={unsubscribe.isPending}
              className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
              onClick={() => stopFollowing(subscription)}
            >
              <XIcon className="size-3.5" aria-hidden />
            </button>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
