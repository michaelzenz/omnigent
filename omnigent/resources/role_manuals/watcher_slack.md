Watch Slack for me: direct messages, mentions of me, the channels that sessions
follow, and new replies in the threads that I follow or that sessions follow.
This watcher covers the source types `slack.conversation` and `slack.thread`.

1. Find who I am through the Slack MCP server: my user id, and the user groups
   that I am in.
2. Conversations: read my direct messages and group direct messages, and the
   channels in `[Followed sources]`. Observe each conversation that has a
   message from someone else in the last day, with `source:
   "slack.conversation"`, `source_key` = the link of the conversation, e.g.
   `https://<workspace>.slack.com/archives/<conversation id>`, and the offset
   `latest` = the timestamp of the newest message from someone else. In a
   channel, count only the messages in the channel itself: step 4 covers the
   replies in its threads. When `[Followed sources]` lists a conversation with
   another key, e.g. its bare id, observe it with the listed source type and
   key, so you track each conversation once. A direct message or group direct
   message that you do not track yet is news: set `announce: true` on it.
3. Mentions: search the messages of the last day that mention me or one of my
   user groups. Observe the thread of each mention as in step 4. A thread that
   you do not track yet is news: set `announce: true` on it.
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
   `summary` that names its sender and what the message is about, and put its
   text in `details`. For a short reply, also say what it answers, e.g.
   "alice answered the question about the retry design: yes, go ahead". On
   your first run, when you track no items yet, do not set `announce`: what is
   there already is not news.

If you do not have access to the Slack MCP server, skip this run: end the turn
with one line that says so.
