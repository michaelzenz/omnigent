import { useState } from "react";
import { ChevronDownIcon, ChevronRightIcon, ExternalLinkIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  type FyiCluster,
  type FyiEvent,
  useDismissFyiCluster,
  useFyiClusters,
} from "@/hooks/useGlobalHubFyi";
import { BOARD_COLUMN_CLASS } from "./boardLayout";
import { OpenSessionLink } from "./OpenSessionLink";

/** The FYI clusters waiting for the user, oldest first; hidden when there are none. */
export function FyiStream() {
  const clusters = useFyiClusters();
  if (clusters.data === undefined) {
    return clusters.isError ? (
      <p role="alert" className={`${BOARD_COLUMN_CLASS} px-4 pt-4 text-sm text-destructive`}>
        Could not load the FYI signals: {clusters.error.message}
      </p>
    ) : null;
  }
  if (clusters.data.length === 0) return null;

  const signals = clusters.data.reduce((count, cluster) => count + cluster.events.length, 0);
  return (
    <section className={`${BOARD_COLUMN_CLASS} space-y-3 px-4 pt-4`} data-testid="globalhub-fyi">
      <h2 className="text-sm font-semibold">
        FYI <span className="ml-1 font-medium text-muted-foreground">({signalCount(signals)})</span>
      </h2>
      {clusters.data.map((cluster) => (
        <FyiClusterCard key={cluster.id} cluster={cluster} />
      ))}
    </section>
  );
}

function FyiClusterCard({ cluster }: { cluster: FyiCluster }) {
  const dismiss = useDismissFyiCluster();
  const [open, setOpen] = useState(true);

  return (
    <article
      className="rounded-xl border border-border bg-card p-3 shadow-sm"
      data-testid={`fyi-cluster-${cluster.id}`}
    >
      <header className="mb-2 border-l-2 border-foreground/25 pl-2.5">
        <p className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
          FYI
        </p>
        <h3 className="text-sm leading-snug font-semibold">{cluster.headline}</h3>
        <p className="mt-0.5 text-xs font-medium text-muted-foreground">
          {signalCount(cluster.events.length)}
        </p>
      </header>
      {cluster.rationale && <p className="mb-3 text-sm leading-relaxed">{cluster.rationale}</p>}
      <div className="mb-3 rounded-md border border-border/70 bg-muted/35">
        <button
          type="button"
          aria-expanded={open}
          className="flex w-full items-center gap-1.5 px-2.5 py-2 text-left text-xs font-medium text-muted-foreground hover:text-foreground"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? (
            <ChevronDownIcon className="size-3.5 shrink-0" aria-hidden />
          ) : (
            <ChevronRightIcon className="size-3.5 shrink-0" aria-hidden />
          )}
          {open ? "Hide signals" : "Show signals"}
        </button>
        {open && (
          <ul className="space-y-1 border-t border-border/60 px-2.5 py-2">
            {cluster.events.map((event) => (
              <li key={event.id} className="flex items-center gap-2 text-sm">
                <span className="size-1.5 shrink-0 rounded-full bg-foreground/45" aria-hidden />
                <span className="min-w-0 flex-1 leading-snug break-words">{event.title}</span>
                <SignalLink event={event} />
              </li>
            ))}
          </ul>
        )}
      </div>
      {dismiss.error && (
        <p role="alert" className="mb-2 text-xs text-destructive">
          {dismiss.error.message}
        </p>
      )}
      <footer className="flex justify-end border-t border-border/50 pt-2">
        <Button
          size="sm"
          variant="outline"
          loading={dismiss.isPending}
          onClick={() => dismiss.mutate(cluster.id)}
        >
          Dismiss
        </Button>
      </footer>
    </article>
  );
}

function SignalLink({ event }: { event: FyiEvent }) {
  if (event.event_type.startsWith("watch.")) {
    const url = event.source_key;
    if (!url || !/^https?:\/\//.test(url)) return null;
    return (
      <Button asChild variant="outline" size="icon-sm" className="text-muted-foreground">
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open the source of ${event.title}`}
          title="Open source"
        >
          <ExternalLinkIcon aria-hidden />
        </a>
      </Button>
    );
  }
  if (!event.source_session_id) return null;
  return (
    <OpenSessionLink
      sessionId={event.source_session_id}
      label={`Open the session for ${event.title}`}
    />
  );
}

function signalCount(count: number): string {
  return count === 1 ? "1 signal" : `${count} signals`;
}
