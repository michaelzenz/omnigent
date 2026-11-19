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
import { followLinkWithPopupFallback } from "@/lib/openLinkInApp";
import { cn } from "@/lib/utils";

/**
 * The sources that the session follows, e.g. the pull requests its agent opened. It shows
 * when the session follows at least one; the user can stop following each one. A source
 * with a web address opens in the user's browser, outside the app.
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
          <div
            key={subscription.id}
            className="flex items-center gap-1 pr-1"
            data-testid={`composer-watching-${subscription.id}`}
          >
            <WatchedSource subscription={subscription} />
            <button
              type="button"
              aria-label={`Stop watching ${subscription.source_key}`}
              disabled={unsubscribe.isPending}
              className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
              onClick={() => stopFollowing(subscription)}
            >
              <XIcon className="size-3.5" aria-hidden />
            </button>
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** One followed source; a web address is a link that opens in the browser, outside the app. */
function WatchedSource({ subscription }: { subscription: SessionSubscription }) {
  const url = /^https?:\/\//i.test(subscription.source_key) ? subscription.source_key : null;
  const title = subscription.note ?? subscription.source_key;
  const label = (
    <span className="min-w-0 flex-1">
      <span className={cn("block truncate", url && "text-primary hover:underline")}>
        {subscription.source_key}
      </span>
      <span className="block truncate font-mono text-xs text-muted-foreground">
        {subscription.source_type}
      </span>
    </span>
  );
  if (url === null) {
    return (
      <DropdownMenuItem
        title={title}
        onSelect={(event) => event.preventDefault()}
        className="min-w-0 flex-1"
      >
        {label}
      </DropdownMenuItem>
    );
  }
  return (
    <DropdownMenuItem asChild title={title} className="min-w-0 flex-1 cursor-pointer">
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(event) => followLinkWithPopupFallback(event, url, undefined)}
      >
        {label}
      </a>
    </DropdownMenuItem>
  );
}
