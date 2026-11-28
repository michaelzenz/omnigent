Watch Slack for me: direct messages, group direct messages, mentions of me,
replies in the threads that I am in, and the channels and threads that
sessions follow. This watcher covers the source types `slack.conversation` and
`slack.thread`.

1. Find who I am through the Slack MCP server: my user id, and the user groups
   that I am in.
2. Find what is new since `[Cursor]`, the timestamp of the newest message that
   you read in your last run. Without a cursor, look at the last two days. Run
   these searches with `search.messages`, sorted by timestamp, newest first,
   with `after:` set to the day before the cursor's date. Page until the
   messages are older than the cursor.
   - `to:me`: the messages in my direct messages and group direct messages.
   - `is:thread with:@me`: the replies in the threads that I started or
     replied in, also when they do not mention me.
   - The messages that mention me or one of my user groups.
   Skip the messages that "Ignore these messages" below lists. Skip also the
   conversations and threads in "Do not watch", except the messages in them
   that mention me.
3. Group the messages that are left:
   - A message in a direct message or a group direct message belongs to its
     conversation. Look at `is_im` and `is_mpim`: a group direct message has
     an id that starts with `C`, as a channel does. Observe the conversation
     with `source: "slack.conversation"`, `source_key` = the link of the
     conversation, e.g. `https://<workspace>.slack.com/archives/<conversation
     id>`, and the offset `latest` = the timestamp of its newest message.
   - A message in a channel thread belongs to its thread: the `thread_ts` in
     the message link names it. Observe the thread with `source:
     "slack.thread"`, `source_key` = the link of its first message, e.g.
     `https://<workspace>.slack.com/archives/<channel id>/p<thread_ts without
     the dot>`, and the offset `replies` = the timestamp of its newest reply.
     A mention outside a thread starts a thread: observe it the same way.
   Build each link from the workspace domain of the search results, the same
   way every time. A conversation or thread that you do not track yet is
   news: set `announce: true` on it.
4. Read what the searches miss: each channel, and each thread that I am not
   in, that `[Followed sources]` or `[Tracked items]` lists, e.g. a thread
   that a session follows. Read a channel's own messages since the cursor, and
   a thread's newest reply. Observe each with the listed source type and key,
   and the offsets of step 3. The replies in a channel's threads are separate
   threads.
5. Send the items in `observe` calls of at most 100 items each, and save
   `cursor` in the last call: the timestamp of the newest message that you
   read. When nothing is new, send only the cursor. When a call fails, do not
   save the cursor: the next run reads the same messages again, and the
   server does not report the saved items twice. Write each link without a
   query (`?...`) or a fragment (`#...`). For a new message, write a one-line
   `summary` that names its sender and what the message is about, and put its
   text in `details`. For a short reply, also say what it answers, e.g.
   "alice answered the question about the retry design: yes, go ahead". On
   your first run, when you track no items yet, do not set `announce`: what is
   there already is not news.

Do not mark Slack items done. The server marks an item done when nothing new
came in it for 14 days, and a new message reopens it.

Your Slack tool can filter message data for privacy and then return a redacted
summary instead of the messages. When it takes an `analysis_prompt`, pass this
one with every read of messages: "Output one JSON object per line for every
message, oldest first, with exactly these keys: ts, thread_ts, user, user_name,
permalink, summary (one line: what the message says or asks, without secrets).
Use null for a missing key. End with one line giving has_more (true or false)
and next_cursor. Output nothing else." Write each change's `summary` from these
lines. When a read still returns only a redacted summary, report those
conversations with `problem`, and do not observe a change for them.

If you do not have access to the Slack MCP server, report it with `problem`
(source `slack.conversation`, no `source_key`) and end the run.

### Ignore these messages

Skip a message of each kind below, unless it mentions me: a bot that mentions
me still counts. Add a kind on its own line.

- My own messages.
- Messages from apps and bots, e.g. ticket, code review, CI or deployment
  notifications: the sender is a bot (`users.info` shows `is_bot`), or the
  message has a `bot_id` or the subtype `bot_message`. Other watchers check
  those systems.
- Messages that a workflow or an integration posts for an app, e.g. a form
  or a reminder.
- Channel events, e.g. a member joined or left, or the topic changed.

### Do not watch

Skip these conversations and threads, by link or name, one on each line, e.g.
`#random`. Mentions of me still count in them.

- (none)
