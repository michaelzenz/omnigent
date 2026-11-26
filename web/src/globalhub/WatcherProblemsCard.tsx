import { ExternalLinkIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  type WatcherProblem,
  type WatcherProblemSource,
  useDismissWatcherProblems,
  useWatcherProblems,
} from "@/hooks/useGlobalHubFyi";
import { formatMessageTimestamp } from "@/lib/relativeTime";
import { BOARD_COLUMN_CLASS } from "./boardLayout";
import { OpenSessionLink } from "./OpenSessionLink";

export function WatcherProblemsCard() {
  const problems = useWatcherProblems();
  const dismiss = useDismissWatcherProblems();
  const watchers = problems.data ?? [];
  if (watchers.length === 0) return null;

  return (
    <div className={`${BOARD_COLUMN_CLASS} px-4 pt-4`}>
      <article
        className="rounded-xl border border-amber-500/40 bg-card p-3 shadow-sm"
        data-testid="globalhub-watcher-problems"
      >
        <header className="mb-2 border-l-2 border-amber-500/70 pl-2.5">
          <p className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
            Watchers
          </p>
          <h3 className="text-sm leading-snug font-semibold">{headline(watchers)}</h3>
        </header>
        <ul className="mb-3 space-y-3">
          {watchers.map((problem) => (
            <WatcherProblemItem key={problem.watcher_id} problem={problem} />
          ))}
        </ul>
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
            onClick={() => dismiss.mutate(watchers.map((problem) => problem.watcher_id))}
          >
            Dismiss
          </Button>
        </footer>
      </article>
    </div>
  );
}

function WatcherProblemItem({ problem }: { problem: WatcherProblem }) {
  return (
    <li className="space-y-1" data-testid={`watcher-problem-${problem.watcher_id}`}>
      <div className="flex items-center gap-2">
        <p className="min-w-0 flex-1 text-sm font-medium break-words">
          {problem.watcher_name}
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            reported {formatMessageTimestamp(problem.reported_at)}
          </span>
        </p>
        {problem.session_id && (
          <OpenSessionLink sessionId={problem.session_id} label={`Open ${problem.watcher_name}`} />
        )}
      </div>
      {problem.run_error && (
        <p className="text-sm break-words">Last run failed: {problem.run_error}</p>
      )}
      {problem.sources.length > 0 && (
        <ul className="space-y-0.5 text-sm">
          {problem.sources.map((source) => (
            <li
              key={`${source.source}|${source.source_key ?? ""}|${source.reason}`}
              className="break-words"
            >
              <SourceLabel source={source} />
              <span className="text-muted-foreground"> — {source.reason}</span>
            </li>
          ))}
        </ul>
      )}
      {problem.note && <p className="text-sm break-words text-muted-foreground">{problem.note}</p>}
    </li>
  );
}

function SourceLabel({ source }: { source: WatcherProblemSource }) {
  const key = source.source_key;
  if (key && /^https?:\/\//.test(key)) {
    return (
      <a
        href={key}
        target="_blank"
        rel="noopener noreferrer"
        className="font-medium text-primary hover:underline"
      >
        {source.source}
        <ExternalLinkIcon className="ml-0.5 inline size-3" aria-hidden />
      </a>
    );
  }
  return <span className="font-medium">{key ? `${source.source} ${key}` : source.source}</span>;
}

function headline(watchers: WatcherProblem[]): string {
  return watchers.length === 1
    ? `${watchers[0].watcher_name} is not working correctly`
    : `${watchers.length} watchers are not working correctly`;
}
