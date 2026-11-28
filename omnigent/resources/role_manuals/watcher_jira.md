Watch Jira for me: new tickets assigned to me, mentions of me, and the tickets
that sessions follow. This watcher covers the source type `jira.issue`.

1. Find who I am through the Jira MCP server: my account id and my display
   name.
2. Assigned tickets: search with the JQL `assignee = currentUser() AND
   resolution = Unresolved ORDER BY created DESC`. Observe each ticket with
   the offsets `status`, `assignee` and `comments` (the id of the newest
   comment that is not mine). A ticket that you do not track yet is news, also
   on your first run: set `announce: true` on it.
3. Mentions: search the tickets updated in the last day whose description or
   comments mention me. Observe each with the offsets of step 2. A ticket that
   you do not track yet is news, also on your first run: set `announce: true`
   on it.
4. Followed tickets: the tickets in `[Followed sources]`. Observe each new
   one with the offsets of step 2. For a done one, observe only its `status`
   offset, to see whether it was reopened.
5. Check the items that you already track: page through `watch_items` with
   `list`. For each ticket that steps 2 to 4 did not find, read its current
   state: mark it `done` when it is resolved or closed, with the reason as
   `summary`. Else observe it again. The server marks a ticket done when
   nothing about it changed for 14 days, and a change reopens it.
6. Send the tickets in `observe` calls of at most 100 tickets each, with
   `source: "jira.issue"` and `source_key` = the ticket key. For a change, write a one-line `summary`
   that names who made the change, and put the summary of the ticket and the
   text of a new comment in `details`.

If you do not have access to the Jira MCP server, report it with `problem`
(source `jira.issue`, no `source_key`) and end the run.
