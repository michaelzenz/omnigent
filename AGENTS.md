# Agent guidance

Guidance for AI agents (Claude Code, Copilot, Cursor, etc.) working in this
repository. See `CONTRIBUTING.md` for the full contributor workflow.

## Committing

Run the `pre-commit` hook before committing (`pre-commit run --all-files`, or
let it run on staged files via `git commit`). Fix any issues it reports so the
commit lands clean — CI runs the same checks.

## Local development shortcuts

Use `just` for common tasks; run `just --list` for grouped recipes.

- `just ensure` — install/check prerequisites
- `just run-ios` / `just run-android` — build/run mobile apps
- `just dev` / `just dev-mobile` — start the omnigent dev pod
- `just electron-dev` / `just electron-build` — Electron desktop shell
- `just lint` / `just lint-all` — run pre-commit
- `just normalize-locks` — rewrite lockfile registries to PyPI/npmjs.org

## Running multiple local instances

`scripts/fork_local_instance.py` spins up an isolated server + Electron
window from a snapshot of your current instance. Each fork gets its own
SQLite DB (online backup), artifacts, port, host daemon, and Electron
user-data-dir (isolated window state), while sharing
`~/.omnigent/config.yaml` for provider credentials.

The fork launches a **separate** Electron process with its own
`--user-data-dir` and `settings.json`, so it auto-connects to the forked
server and does not interfere with the main Electron window.

```bash
# Fork the default instance into "experiment-a" (builds web UI, runs DB
# migrations, starts server + host daemon, opens isolated Electron window)
./scripts/fork_local_instance.py experiment-a

# Fork from a specific source instance
./scripts/fork_local_instance.py experiment-b \
  --source-data-dir ~/.omnigent/instances/experiment-a

# Fork without building the web UI (use whatever is already built)
./scripts/fork_local_instance.py experiment-c --no-build-web

# Fork without opening an Electron window
./scripts/fork_local_instance.py experiment-c --no-open

# Stop the instance (server + daemon + Electron window)
./scripts/fork_local_instance.py --stop experiment-a

# Stop without closing the Electron window
./scripts/fork_local_instance.py --stop experiment-a --no-close-window
```

Stopped instances keep their files under `~/.omnigent/instances/<name>/` for
inspection. Delete with `rm -rf ~/.omnigent/instances/<name>`.

## Pull requests

When you open a pull request, fill in the repo's PR template at
`.github/pull_request_template.md` (case-sensitive on Linux — note the lowercase
filename). Keep every section and checkbox row so reviewers can skim them.

- **Summary** — what changed and why.
- **Test Plan** — how you verified it.
- **Demo** — a **video or images** showing the change. Expected on contributor
  PRs for UI / frontend changes (check the "UI / frontend change" box under
  *Type of change*) so reviewers can see the new behaviour without checking out
  the branch. Use `N/A` for non-visual changes.
- **Type of change** / **Test coverage** — check all that apply (at least one
  each).
- **Coverage notes** — required if you checked "Manual verification completed"
  or "Not applicable".

Generate the description from the actual diff and this session's context — lead
with the motivation, then the change. Don't pass a `--body` that skips these
sections.

## Finishing a task

When you finish a task, print instructions to the user on how to test it: the
commands to run, the inputs to provide, or the steps to reproduce so they can
verify the result themselves. Prefer verification that is best performed by a
human, such as concrete manual behavior checks, rather than only listing unit
test commands. Don't leave the user guessing how to confirm the work — tell
them exactly what to do.

## Deprecating features

When deprecating a feature, note the version in which it is expected to be
removed so we can clean it up when that version ships. Call out the deprecation
version in code (e.g. a `@deprecated` tag or comment naming the target release)
and in the PR/commit description, so there's a clear marker to act on later.

## Code comments

Keep comments short and focused on the code, not on the change history.

- **Keep them brief** — prefer one or two lines. Avoid comments longer than
  three lines; if you need more, the code likely needs refactoring or a doc
  string, not a wall of inline commentary.
- **Describe the scenario, not the PR** — explain *what* the code handles or
  *why* it exists, in terms a future reader needs. Don't reference PR numbers,
  issue numbers, or ticket IDs (e.g. `#1646`, `fixes JIRA-123`); the scenario
  should be clear without chasing external links.

## Database query names

Application stores use `make_named_managed_session_maker` and give every
session a stable semantic operation name. The session-level name must describe
the caller's intent rather than repeat SQL syntax; use a nested
`query_name_scope` only when one transaction needs distinct names for important
subqueries. Because the named session covers implicit flush and commit, don't
add an explicit `flush()` only to make a query name observable.

## Supporting models in server-brokered Pi

Server-brokered Pi supports three Databricks inference surfaces. Route models by
the API the selected **model ID** accepts, not just by vendor family:

| Model ID | Surface |
| --- | --- |
| Claude | Anthropic Messages |
| GPT | OpenAI Responses |
| `system.ai.kimi-*`, `system.ai.glm-*`, Inkling, Qwen 3 | OpenAI Responses |
| `databricks-kimi-*`, `databricks-glm-*`, and other serving-endpoint aliases | OpenAI Chat Completions |
| Other `system.ai.*` models | MLflow (not supported by the broker) |

The spelling matters: equivalent `system.ai.*` and `databricks-*` aliases can
support different APIs. Prefer the live Unity Catalog model-services metadata
when available; `omnigent/pi_model_compatibility.py` is the fallback classifier
when metadata is absent.

When adding model support:

1. Confirm the exact model ID and supported API types from the live catalog or a
   direct workspace probe. Do not infer the surface solely from the model name.
2. Update `databricks_pi_surface_for_model()` only if the metadata-free fallback
   needs a new family rule. Keep unsupported models fail-loud.
3. Ensure `omnigent/pi_native_credentials.py` registers the model under the Pi
   provider whose wire API matches that surface.
4. If adding a new surface, update the exact-path allowlists in both
   `omnigent/host/inference_relay.py` and
   `omnigent/server/routes/inference_proxy.py`; never add a catch-all proxy.
5. Keep `omnigent/inference_proxy.py` model-to-surface validation consistent
   with provider registration.
6. Add tests for both `system.ai.*` and `databricks-*` spellings, exact accepted
   paths, near-match rejection, and an end-to-end streamed response. A model is
   not considered supported from classifier coverage alone.

The current exact upstream routes are:

```text
anthropic   -> /ai-gateway/anthropic/v1/messages
responses   -> /ai-gateway/openai/v1/responses
completions -> /serving-endpoints/chat/completions
```

This broker applies to the headless `pi` harness only. Native and other SDK/CLI
harnesses retain their own provider and authentication behavior.

## Framework-owned instructions

Keep runtime lifecycle and metadata instructions separate from portable agent
instructions:

- Agent-spec and per-request instructions are user-authored. Framework-owned
  instructions are additive runtime behavior and are appended after them in
  `omnigent/runtime/prompt.py`.
- Keep the canonical instruction text and lifecycle gate in the owning framework
  module. Harness adapters should only transport the composed instructions; do
  not duplicate policy across adapters or add lifecycle metadata to `AgentSpec`.
- If framework instructions grow beyond a small ordered list, introduce a
  structured `FrameworkInstructions` value at the prompt-composition boundary.
