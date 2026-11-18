Watch Slack for me: direct messages, mentions of me, and new replies in the
threads that I follow or that sessions follow. This watcher covers the source
types `slack.conversation` and `slack.thread`.

1. Find who I am through the Slack MCP server: my user id, and the user groups
   that I am in.
2. Direct messages: read my direct messages and group direct messages. Observe
   each conversation that has a message from someone else in the last day,
   with `source: "slack.conversation"`, `source_key` = the link of the
   conversation, e.g. `https://<workspace>.slack.com/archives/<conversation
   id>`, and the    offset `latest` = the timestamp of the newest message from
   someone else. A conversation that you do not track yet is news, also on
   your first run: set `announce: true` on it.
3. Mentions: search the messages of the last day that mention me or one of my
   user groups. Observe the thread of each mention as in step 4. A thread that
   you do not track yet is news, also on your first run: set `announce: true`
   on it.
4. Threads that I follow: the threads that I started, replied to, or was
   mentioned in, and the threads in `[Followed sources]`. Search my messages
   of the last day to find new ones. Observe each thread with `source:
   "slack.thread"`, `source_key` = the link of its first message, e.g.
   `https://<workspace>.slack.com/archives/<channel id>/p<timestamp>`, and the
   offset `replies` = the timestamp of its newest reply from someone else.
5. Check the items that you already track: page through `watch_items` with
   `list`. For each item that steps 2 to 4 did not find, read its newest
   message: mark the item `done` when it has no new message for 7 days, with
   `summary: "Quiet for 7 days"`, else observe it again. A new message in a
   done thread reopens it.
6. Send all items in one `observe` call. Write each link without a query
   (`?...`) or a fragment (`#...`). For a new message, write a one-line
   `summary` that names its sender, and put its text in `details`.

If you do not have access to the Slack MCP server, skip this run: end the turn
with one line that says so.
