Watch GitHub for me: review requests to me or my teams, news on my own pull
requests, and the pull requests that sessions follow. This watcher covers the
source type `github.pull_request`.

1. Find who I am through the GitHub MCP server: the login of the signed-in
   user.
2. Review requests: search the open pull requests that request a review from
   me or from one of my teams, with `is:pr is:open archived:false
   review-requested:@me`. This search also finds the requests to my teams.
   Observe each with the offsets `state` and `requested` = `"me"`. A review
   request that you do not track yet is news, also on your first run: set
   `announce: true` on it.
3. My pull requests: search `is:pr is:open archived:false author:@me`. For
   each, read the CI result on the head commit, the review decision, and the
   newest comment or review comment that is not mine. A comment from a person
   or from an agent counts; a bot that only posts check results does not.
   Observe each with the offsets `state`, `checks` (the result and the head
   commit, e.g. `"failure@abc123"`), `review` and `comments` (the id of that
   newest comment).
4. Followed pull requests: the pull requests in `[Followed sources]`. Observe
   each new one with the offsets of step 3. For a done one, observe only its
   `state` offset, to see whether it was reopened.
5. Check the items that you already track: page through `watch_items` with
   `list`. For each item that steps 2 to 4 did not find, read its pull
   request: mark the item `done` when the pull request is merged or closed, or
   when the item was a review request that no longer asks me or my teams, with
   the reason as `summary`, e.g. `"Merged"`. Else observe it again.
6. Send the pull requests in `observe` calls of at most 100 pull requests
   each, with `source: "github.pull_request"` and `source_key` = the canonical
   URL. Observe a pull
   request that is in more than one group once, with all of its offsets. For a
   failed check or a new comment, write a one-line `summary` that names who
   made the change, and put the names of the failing checks or the text of the
   comment in `details`.

If you do not have access to the GitHub MCP server, report it with `problem`
(source `github.pull_request`, no `source_key`) and end the run.
