Watch Jira for me: new tickets assigned to me, and mentions of me.

1. Find who I am through the Jira MCP server: my account id and my display
   name.
2. Assigned tickets: search with the JQL `assignee = currentUser() AND
   resolution = Unresolved ORDER BY created DESC`. Observe each ticket with
   the offsets `status`, `assignee` and `comments` (the id of the newest
   comment that is not mine). A ticket that you do not track yet is news: set
   `announce: true` on it.
3. Mentions: search the tickets updated in the last day whose description or
   comments mention me. Observe each with the offsets of step 2. A ticket that
   you do not track yet is news: set `announce: true` on it.
4. Check the items that you already track: page through `watch_items` with
   `list`. For each ticket that steps 2 and 3 did not find, read its current
   state: mark it `done` when it is resolved or closed, or when it is not
   assigned to me and has no new comment for 7 days. Else observe it again.
5. Send all tickets in one `observe` call, with `source: "jira"` and
   `source_key` = the ticket key. For a change, write a one-line `summary`, and
   put the summary of the ticket and the text of a new comment in `details`.
   On your first run, when you track no items yet, do not set `announce`:
   what is there already is not news.

If you do not have access to the Jira MCP server, skip this run: end the turn
with one line that says so.
