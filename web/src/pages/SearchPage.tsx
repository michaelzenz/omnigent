import { useEffect, useMemo, useState } from "react";
import { ArchiveIcon, Loader2Icon, SearchIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConversations } from "@/hooks/useConversations";
import { relativeTime } from "@/lib/relativeTime";
import { useNavigate, useSearchParams } from "@/lib/routing";
import { HighlightedText } from "@/shell/CommandPalette";
import { conversationDisplayLabel, getConversationAgentType } from "@/shell/sidebarNav";

export function SearchPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const searchedQuery = (params.get("q") ?? "").trim();
  const [draft, setDraft] = useState(searchedQuery);
  const conversations = useConversations(searchedQuery, true);

  useEffect(() => {
    setDraft(searchedQuery);
  }, [searchedQuery]);

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = conversations;
  useEffect(() => {
    if (searchedQuery && hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [searchedQuery, hasNextPage, isFetchingNextPage, fetchNextPage]);

  const results = useMemo(() => {
    const seen = new Set<string>();
    return (conversations.data?.pages ?? [])
      .flatMap((page) => page.data)
      .filter((conversation) => {
        if (seen.has(conversation.id)) return false;
        seen.add(conversation.id);
        return true;
      });
  }, [conversations.data]);

  const submit = (): void => {
    const query = draft.trim();
    if (!query) return;
    setParams({ q: query });
  };

  return (
    <PageScroll maxWidthClassName="max-w-none" contentClassName="px-6 py-8 md:px-10">
      <main className="flex w-full flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold">Search chats</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Search active and archived chats by title or message content.
          </p>
        </div>

        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <div className="relative flex-1">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              className="pl-9"
              placeholder="Search all chats"
              aria-label="Search all chats"
            />
          </div>
          <Button type="submit" disabled={!draft.trim()}>
            Search
          </Button>
        </form>

        {searchedQuery && (
          <div className="flex items-center justify-between gap-4">
            <p className="text-ui text-muted-foreground">
              {conversations.isLoading
                ? "Searching…"
                : `${results.length} result${results.length === 1 ? "" : "s"} for “${searchedQuery}”`}
            </p>
            {(conversations.isFetching || isFetchingNextPage) && !conversations.isLoading && (
              <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
            )}
          </div>
        )}

        {conversations.isError && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-ui text-destructive">
            Search failed. Please try again.
          </div>
        )}

        {!searchedQuery ? (
          <div className="rounded-xl border border-dashed p-10 text-center text-muted-foreground">
            Enter a search term to find chats.
          </div>
        ) : !conversations.isLoading && results.length === 0 ? (
          <div className="rounded-xl border border-dashed p-10 text-center text-muted-foreground">
            No matching chats found.
          </div>
        ) : (
          <div className="grid gap-3">
            {results.map((conversation) => (
              <button
                key={conversation.id}
                type="button"
                onClick={() => navigate(`/c/${conversation.id}`)}
                className="rounded-xl border border-border bg-card p-4 text-left transition-colors hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <h2 className="font-medium">
                      <HighlightedText
                        text={conversationDisplayLabel(conversation)}
                        query={searchedQuery}
                      />
                    </h2>
                    <p className="mt-0.5 text-sm text-muted-foreground">
                      {getConversationAgentType(conversation)} ·{" "}
                      {relativeTime(conversation.updated_at)}
                    </p>
                  </div>
                  {conversation.archived && (
                    <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground">
                      <ArchiveIcon className="size-3" />
                      Archived
                    </span>
                  )}
                </div>
                {conversation.search_snippet && (
                  <p
                    data-testid="search-result-excerpt"
                    className="mt-3 line-clamp-6 whitespace-pre-wrap text-ui leading-6 text-muted-foreground"
                  >
                    <HighlightedText text={conversation.search_snippet} query={searchedQuery} />
                  </p>
                )}
              </button>
            ))}
          </div>
        )}
      </main>
    </PageScroll>
  );
}
