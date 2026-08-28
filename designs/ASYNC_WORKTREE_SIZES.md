# Async Worktree Size Calculation

## Overview

When the user selects a workspace that is a git repository in the New Session page, the host daemon periodically (every 10 minutes) calculates the on-disk size of each git worktree belonging to that repository. The results are cached on the host and served to the UI, which displays a clickable summary line below the workspace / auto-worktree button row. Clicking the line opens a popover showing each worktree's path and size.

## Architecture

```
┌──────────────┐     REST      ┌──────────┐    WebSocket    ┌──────────────┐
│  Web UI      │──────────────▶│  Server  │────────────────▶│  Host Daemon │
│              │  GET worktree- │          │  host.worktree_  │              │
│  NewChat     │  sizes         │          │  sizes frame     │  Background  │
│  Dialog      │◀──────────────│          │◀────────────────│  size calc   │
│              │               │          │  result frame    │  (10 min)    │
└──────────────┘               └──────────┘                  └──────────────┘
```

The host daemon runs the size calculation as a **background asyncio task** (not a PollScheduler plugin — the workspace path is session-specific and changes as the user types, while PollScheduler plugins are ambient and host-wide). Instead, the host maintains an in-memory **WorktreeSizeCache** keyed by repo root path.

### Caching model — cache never expires

The cache is **never invalidated**. Any cached result is always returned, regardless of how old it is. The UI shows the cached data alongside a "updated Xm ago" timestamp so the user knows how fresh the numbers are.

The 10-minute interval is a **recalculation trigger**, not a TTL:
- When a request arrives and the cache has data, the host returns it **immediately** (non-blocking).
- If the last calculation was more than 10 minutes ago (or there is no cache), the host **also** kicks off a background recalculation — but it does NOT block the request. The old cached data is returned right away; the updated data lands in the cache and is picked up by the UI's next refetch.
- The only blocking case is the **very first request** for a repo with no cache — the host must calculate before it can return anything. The UI shows a loading spinner during this.
- A **refresh button** in the UI sends `force=true`, which tells the host to recalculate immediately regardless of when the last calculation was. This request blocks until the new calculation completes (the UI shows a spinner on the button).

### When do we fire?

The calculation is triggered when the **user selects a workspace** that is a git repository in the New Session dialog. The `useWorktreeSizes` React Query hook fires when `isGitWorkspace` becomes true. The UI then polls every 10 minutes (`refetchInterval: 600_000`) to pick up background-refreshed data. No workspace selected → no request → no CPU/IO spent.

---

## 1. Host: Size Calculation (`omnigent/host/worktree_sizes.py`)

New module. Pure functions + a cache class. No dependency on the server or tunnel.

### `WorktreeSizeEntry` (dataclass)

```python
@dataclass
class WorktreeSizeEntry:
    path: str           # absolute worktree dir
    branch: str | None  # from git worktree list
    is_main: bool
    size_bytes: int     # total bytes on disk (du-style, following no symlinks)
```

### `WorktreeSizeResult` (dataclass)

```python
@dataclass
class WorktreeSizeResult:
    repo_root: str
    entries: list[WorktreeSizeEntry]
    total_bytes: int
    calculated_at: float  # monotonic time
    error: str | None = None
```

### `WorktreeSizeCache`

Thread-safe in-memory cache. Keyed by repo root path. **Never expires** — cached data is always returned; the 10-min interval only controls when a background recalculation is triggered.

```python
class WorktreeSizeCache:
    def __init__(self, recalc_interval_s: float = 600.0) -> None:  # 10 minutes
        self._lock = threading.Lock()
        self._cache: dict[str, WorktreeSizeResult] = {}
        self._recalc_interval_s = recalc_interval_s
        self._in_flight: set[str] = set()  # repo roots currently calculating

    def get(self, repo_root: str) -> WorktreeSizeResult | None:
        """Return cached result (always, regardless of age), or None when no cache exists."""

    def put(self, repo_root: str, result: WorktreeSizeResult) -> None:
        """Store a result (overwrites previous)."""

    def needs_recalc(self, repo_root: str) -> bool:
        """True when no cache exists OR last calculation was more than recalc_interval_s ago."""

    def mark_in_flight(self, repo_root: str) -> bool:
        """Atomically claim a repo root for calculation. Returns False if already in-flight."""

    def clear_in_flight(self, repo_root: str) -> None:
        """Release the in-flight marker."""
```

### `calculate_worktree_sizes(repo_path: str) -> WorktreeSizeResult`

The core calculation. Steps:

1. **Resolve main work tree** — reuse `git_worktree._main_work_tree(repo_path)`.
2. **List worktrees** — reuse `git_worktree.list_worktrees(repo_path=repo_root)`.
3. **Size each worktree** — run `du` in a **low-priority subprocess**:

```python
def _dir_size_bytes(path: str) -> int:
    """Calculate total size of a directory tree using du, with CPU/IO priority."""
    # Build command: nice -n 19 ionice -c 3 du -sb <path>
    # (on macOS, ionice is unavailable; use nice -n 19 du -sk <path> * 1024)
    cmd = ["du", "-sb", path]
    if sys.platform == "linux":
        cmd = ["nice", "-n", "19", "ionice", "-c", "3"] + cmd
    elif sys.platform == "darwin":
        cmd = ["nice", "-n", "19", "du", "-sk", path]  # -sk = KB blocks
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if result.returncode != 0:
        return 0
    # du -sb prints "<bytes>\t<path>"; du -sk prints "<KB>\t<path>"
    size_str = result.stdout.split("\t")[0].strip()
    if sys.platform == "darwin":
        return int(size_str) * 1024
    return int(size_str)
```

**CPU/IO restrictions:**
- `nice -n 19` — lowest CPU scheduling priority (Linux + macOS)
- `ionice -c 3` — idle IO class (Linux only; macOS has no equivalent)
- `timeout=300` — 5-minute cap per worktree so a giant repo can't hang forever (v1)
- Called from a background `asyncio.to_thread` so the event loop stays responsive
- One worktree at a time (no parallelism) to avoid IO amplification

### Background calculation flow

The host daemon's frame handler calls this. `force=True` when the user clicks the refresh button.

```python
async def get_or_calculate_worktree_sizes(
    cache: WorktreeSizeCache,
    repo_path: str,
    force: bool = False,
) -> WorktreeSizeResult:
    repo_root = _main_work_tree(repo_path)
    cached = cache.get(repo_root)

    if cached is not None and not force:
        # Always return cached data immediately (never expires).
        # If 10 min have passed, kick off a background recalc — but don't block.
        if cache.needs_recalc(repo_root) and cache.mark_in_flight(repo_root):
            asyncio.create_task(_background_recalc(cache, repo_root, repo_path))
        return cached

    # No cache (first request) or force=True (refresh button):
    # block until the calculation completes.
    if not cache.mark_in_flight(repo_root):
        # Another request is already calculating — wait for it.
        for _ in range(180):  # up to 180s (covers the 300s du timeout with margin)
            await asyncio.sleep(1.0)
            cached = cache.get(repo_root)
            if cached is not None:
                return cached
        return cached or WorktreeSizeResult(
            repo_root=repo_root, entries=[], total_bytes=0,
            calculated_at=time.monotonic(), error="size calculation timed out",
        )
    try:
        result = await asyncio.to_thread(calculate_worktree_sizes, repo_path)
        cache.put(repo_root, result)
        return result
    finally:
        cache.clear_in_flight(repo_root)


async def _background_recalc(cache: WorktreeSizeCache, repo_root: str, repo_path: str) -> None:
    """Background recalculation — updates the cache without blocking any request."""
    try:
        result = await asyncio.to_thread(calculate_worktree_sizes, repo_path)
        cache.put(repo_root, result)
    except Exception:
        logging.warning("Background worktree size recalc failed for %s", repo_root, exc_info=True)
    finally:
        cache.clear_in_flight(repo_root)
```

---

## 2. Host Frames (`omnigent/host/frames.py`)

### New frame kinds

```python
class HostFrameKind(str, Enum):
    ...
    WORKTREE_SIZES = "host.worktree_sizes"
    WORKTREE_SIZES_RESULT = "host.worktree_sizes_result"
```

### `HostWorktreeSizesFrame` (Server → Host)

```python
@dataclass
class HostWorktreeSizesFrame:
    """Request cached worktree sizes for a repository."""
    request_id: str
    repo_path: str  # absolute path inside the repo
    force: bool = False  # True = recalculate immediately (refresh button)
```

### `HostWorktreeSizesResultFrame` (Host → Server)

```python
@dataclass
class HostWorktreeSizesResultFrame:
    """Cached worktree sizes for a repository."""
    request_id: str
    status: str  # "ok" | "failed"
    worktrees: list[dict] | None  # [{path, branch, is_main, size_bytes}, ...]
    total_bytes: int  # sum of all entries
    calculated_at: float  # monotonic seconds — 0 when unknown
    error: str | None = None
```

Add to `HostFrame` union, `encode_host_frame`, and `_decode_known_host_frame`.

---

## 3. Host Daemon: Frame Handler (`omnigent/host/daemon_launch.py` or equivalent receive loop)

The host's WebSocket receive loop handles `host.worktree_sizes` frames:

```python
case HostFrameKind.WORKTREE_SIZES:
    frame = _decode_worktree_sizes(msg)
    asyncio.create_task(
        _handle_worktree_sizes_request(frame, ws_send, size_cache)
    )
```

```python
async def _handle_worktree_sizes_request(frame, ws_send, cache):
    try:
        result = await get_or_calculate_worktree_sizes(cache, frame.repo_path, force=frame.force)
        worktrees = [
            {"path": e.path, "branch": e.branch, "is_main": e.is_main, "size_bytes": e.size_bytes}
            for e in result.entries
        ]
        reply = encode_host_frame(HostWorktreeSizesResultFrame(
            request_id=frame.request_id,
            status="ok",
            worktrees=worktrees,
            total_bytes=result.total_bytes,
            calculated_at=result.calculated_at,
        ))
    except WorktreeError as exc:
        reply = encode_host_frame(HostWorktreeSizesResultFrame(
            request_id=frame.request_id,
            status="failed",
            worktrees=None,
            total_bytes=0,
            calculated_at=0,
            error=exc.message,
        ))
    await ws_send(reply)
```

The `WorktreeSizeCache` instance is created once at daemon startup and passed to the handler.

---

## 4. Server: Proxy + REST Route

### Proxy (`omnigent/server/routes/_host_worktree.py`)

Add `worktree_sizes_on_host`:

```python
async def worktree_sizes_on_host(
    *,
    host_registry: HostRegistry,
    host_conn: HostConnection,
    repo_path: str,
    force: bool = False,
) -> dict[str, object]:
    """Send host.worktree_sizes and await the cached result."""
    request_id = secrets.token_hex(8)
    frame = encode_host_frame(HostWorktreeSizesFrame(
        request_id=request_id,
        repo_path=repo_path,
        force=force,
    ))
    result = await _await_host_worktree_result(
        host_registry=host_registry,
        host_conn=host_conn,
        pending=host_conn.pending_worktree_sizes,  # new dict on HostConnection
        request_id=request_id,
        frame=frame,
        op="worktree sizes",
        timeout_s=310.0,  # slightly more than the 300s du timeout
    )
    if result.get("status") != "ok":
        raise WorktreeProxyError(
            f"worktree sizes failed: {result.get('error') or 'host reported no detail'}"
        )
    return result
```

### REST Route (`omnigent/server/routes/hosts.py`)

```python
@router.get("/hosts/{host_id}/worktree-sizes")
async def get_host_worktree_sizes(
    request: Request,
    host_id: str,
    path: str = Query(...),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Return cached on-disk sizes of each worktree in a repository.

    When ``force=true``, the host recalculates immediately (refresh button).
    """
    # Same auth/ownership/offline pattern as list_host_worktrees
    ...
    result = await worktree_sizes_on_host(
        host_registry=host_registry,
        host_conn=conn,
        repo_path=path,
        force=force,
    )
    return {
        "object": "worktree_sizes",
        "data": result.get("worktrees") or [],
        "total_bytes": result.get("total_bytes", 0),
        "calculated_at": result.get("calculated_at", 0),
    }
```

### `HostConnection` (`omnigent/server/host_registry.py`)

Add `pending_worktree_sizes: dict[str, asyncio.Future]` field, and handle the result frame in the tunnel receive loop (add `HostFrameKind.WORKTREE_SIZES_RESULT` to the match).

---

## 5. Frontend: Hook (`web/src/hooks/useWorktreeSizes.ts`)

```typescript
export interface WorktreeSize {
  path: string;
  branch: string | null;
  is_main: boolean;
  size_bytes: number;
  error: string | null;  // per-worktree error (e.g. "du timed out")
}

export interface WorktreeSizesResponse {
  data: WorktreeSize[];
  total_bytes: number;
  calculated_at: number;  // 0 = not yet calculated
  error: string | null;   // overall error (e.g. "du not found", "not a git repo")
}

export function useWorktreeSizes(hostId: string | null, repoPath: string | null) {
  return useQuery({
    queryKey: ["worktree-sizes", hostId, repoPath],
    queryFn: async () => {
      const params = new URLSearchParams({ path: repoPath! });
      const res = await authenticatedFetch(
        `/v1/hosts/${encodeURIComponent(hostId!)}/worktree-sizes?${params}`,
      );
      if (res.status === 400 || res.status === 404) return null; // not a git repo
      if (!res.ok) throw new Error(`worktree sizes fetch failed: HTTP ${res.status}`);
      const body = await res.json();
      return {
        data: body.data as WorktreeSize[],
        total_bytes: body.total_bytes as number,
        calculated_at: body.calculated_at as number,
        error: (body.error as string | null) ?? null,
      } satisfies WorktreeSizesResponse;
    },
    enabled: hostId !== null && repoPath !== null && repoPath !== "",
    staleTime: 60_000,        // re-fetch at most once per minute
    refetchInterval: 600_000, // poll every 10 min to pick up background-refreshed data
  });
}

/** Force a recalculation (refresh button). Returns a mutation. */
export function useRefreshWorktreeSizes(hostId: string | null, repoPath: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const params = new URLSearchParams({ path: repoPath!, force: "true" });
      const res = await authenticatedFetch(
        `/v1/hosts/${encodeURIComponent(hostId!)}/worktree-sizes?${params}`,
      );
      if (!res.ok) throw new Error(`worktree sizes refresh failed: HTTP ${res.status}`);
      const body = await res.json();
      return body as WorktreeSizesResponse;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["worktree-sizes", hostId, repoPath], data);
    },
  });
}
```

### Size formatting utility (`web/src/lib/formatBytes.ts`)

```typescript
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
```

---

## 6. Frontend: UI in `NewChatDialog.tsx`

### Placement

The summary line renders **below** the workspace picker chip + auto-worktree toggle + git worktree chip row, only when:
- `isGitWorkspace` is true (the selected path is a git repo)
- `worktreeSizes` returned data with ≥ 1 worktree

### Summary line (always visible when data exists)

The summary line is always the same 1-line format with hover underline, regardless of state:

- **Normal**: `3 worktrees · 2.4 GB total · updated 3 min ago`
- **Partial error**: `3 worktrees · 1.2 GB total · 1 error · updated 3 min ago` (the "1 error" part is amber)
- **All failed**: `⚠ Size calculation failed · updated 5 min ago` (entire line is amber)
- **Loading (no cache)**: `Calculating worktree sizes…` with a spinner, no underline (not clickable)

```tsx
{isGitWorkspace && (() => {
  if (worktreeSizesLoading && !worktreeSizes) {
    // First request, no cache yet
    return (
      <div className="mt-1 flex items-center gap-1.5 pl-0.5 text-xs text-muted-foreground">
        <Loader2Icon className="size-3 animate-spin" />
        <span>Calculating worktree sizes…</span>
      </div>
    );
  }
  if (worktreeSizes?.error) {
    // All worktrees failed
    return (
      <div className="mt-1 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setWorktreeSizePopoverOpen(true)}
          className="text-xs text-amber-500 hover:underline transition-colors"
          data-testid="new-chat-worktree-sizes-summary"
        >
          ⚠ Size calculation failed · updated {timeAgo(worktreeSizes.calculated_at)}
        </button>
        <RefreshButton onClick={() => refreshWorktreeSizes.mutate()} loading={refreshWorktreeSizes.isPending} />
      </div>
    );
  }
  if (worktreeSizes?.data && worktreeSizes.data.length > 0) {
    const errorCount = worktreeSizes.data.filter((wt) => wt.error).length;
    return (
      <div className="mt-1 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setWorktreeSizePopoverOpen(true)}
          className="text-xs text-muted-foreground hover:underline hover:text-foreground transition-colors"
          data-testid="new-chat-worktree-sizes-summary"
        >
          {worktreeSizes.data.length} worktree{worktreeSizes.data.length > 1 ? "s" : ""}
          {" · "}
          {formatBytes(worktreeSizes.total_bytes)} total
          {errorCount > 0 && (
            <span className="text-amber-500"> · {errorCount} error{errorCount > 1 ? "s" : ""}</span>
          )}
          {" · updated "}{timeAgo(worktreeSizes.calculated_at)}
        </button>
        <RefreshButton onClick={() => refreshWorktreeSizes.mutate()} loading={refreshWorktreeSizes.isPending} />
      </div>
    );
  }
  return null;
})()}
```

Where `RefreshButton` is a small reusable component:
```tsx
function RefreshButton({ onClick, loading }: { onClick: () => void; loading: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className="flex items-center justify-center size-4 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40"
      title="Refresh worktree sizes"
      data-testid="new-chat-worktree-sizes-refresh"
    >
      {loading ? <Loader2Icon className="size-3 animate-spin" /> : <RotateCwIcon className="size-3" />}
    </button>
  );
}
```}
```

**Hover behavior:** The `hover:underline` class adds the underline on hover, signaling interactivity.

### Popover (mid-size form)

A `Popover` showing each worktree's path and size in a compact table:

```tsx
<Popover open={worktreeSizePopoverOpen} onOpenChange={setWorktreeSizePopoverOpen}>
  <PopoverTrigger asChild>
    <span className="hidden" />  {/* trigger is the summary line above; popover is controlled */}
  </PopoverTrigger>
  <PopoverContent
    align="start"
    className="w-[min(480px,calc(100vw-2rem))] p-3"
  >
    <div className="flex items-center justify-between pb-2 border-b border-border mb-2">
      <span className="text-sm font-medium">Worktree Sizes</span>
      {worktreeSizes?.error ? (
        <span className="text-xs text-amber-500">error</span>
      ) : worktreeSizes?.calculated_at ? (
        <span className="text-xs text-muted-foreground">
          updated {timeAgo(worktreeSizes.calculated_at)}
        </span>
      ) : (
        <Loader2Icon className="size-3 animate-spin text-muted-foreground" />
      )}
    </div>
    {worktreeSizes?.error ? (
      <div className="flex items-start gap-2 py-2 text-xs text-amber-500">
        <TriangleAlertIcon className="size-3.5 shrink-0 mt-0.5" />
        <span>{worktreeSizes.error}</span>
      </div>
    ) : (
      <div className="flex flex-col gap-1.5 max-h-64 overflow-y-auto">
        {worktreeSizes?.data.map((wt) => (
          <div
            key={wt.path}
            className="flex items-center justify-between gap-3 text-xs"
          >
            <div className="flex min-w-0 items-center gap-1.5">
              {wt.is_main ? (
                <FolderIcon className="size-3 shrink-0 text-muted-foreground" />
              ) : (
                <GitBranchIcon className="size-3 shrink-0 text-muted-foreground" />
              )}
              <span className="truncate" title={wt.path}>
                {worktreePathTail(wt.path)}
              </span>
              {wt.branch && (
                <span className="shrink-0 text-muted-foreground/60">({wt.branch})</span>
              )}
            </div>
            {wt.error ? (
              <span
                className="shrink-0 text-amber-500"
                title={wt.error}
              >
                error
              </span>
            ) : (
              <span className="shrink-0 font-medium tabular-nums">
                {formatBytes(wt.size_bytes)}
              </span>
            )}
          </div>
      ))}
      </div>
    )}
    <div className="flex items-center justify-between pt-2 mt-2 border-t border-border">
      <span className="text-xs font-medium">
        {errorCount > 0
          ? `Total (${succeededCount} of ${worktreeSizes.data.length} succeeded)`
          : "Total"}
      </span>
      <span className="text-xs font-medium tabular-nums">
        {formatBytes(worktreeSizes?.total_bytes ?? 0)}
      </span>
    </div>
  </PopoverContent>
</Popover>
```

The popover uses `worktreePathTail()` (already in `NewChatDialog.tsx`) to shorten long paths to their last two segments.

---

## 7. Data Flow Summary

```
1. User selects workspace "/Users/alice/myrepo" in NewChatDialog
2. useHostRepository detects isGitRepository=true, worktrees=[...]
3. useWorktreeSizes(hostId, "/Users/alice/myrepo") fires
4. GET /v1/hosts/{id}/worktree-sizes?path=/Users/alice/myrepo
5. Server → host: host.worktree_sizes frame
6. Host: check WorktreeSizeCache for repo_root
   a. Cache exists (any age) → return immediately; if >10 min since last calc, kick off background recalc (non-blocking)
   b. No cache (first request) → block and calculate, then return
      - resolve main work tree
      - list worktrees via git
      - for each worktree: run `nice -n 19 ionice -c 3 du -sb <path>` (300s timeout)
      - sum sizes, store in cache, return result
7. Host → server: host.worktree_sizes_result frame
8. Server → UI: {object: "worktree_sizes", data: [...], total_bytes: N, calculated_at: T}
9. UI renders: "3 worktrees · 2.4 GB total" (clickable, hover underline) + refresh button
10. UI refetches every 10 min (refetchInterval: 600_000) to pick up background-refreshed data
11. User clicks summary → popover with per-worktree path + size table
12. User clicks refresh → force=true → host recalculates immediately → UI updates
```

---

## 8. Edge Cases

| Case | Behavior |
|------|----------|
| Not a git repo | `useWorktreeSizes` disabled (gated on `isGitWorkspace`); summary line hidden |
| No worktrees (single main) | Summary shows "1 worktree · X total"; popover shows the main worktree |
| Host offline | REST route returns 409; `useWorktreeSizes` error → summary line hidden |
| `du` times out (300s) | That worktree's `size_bytes` is 0 in the result, with a per-entry `error` field; the popover shows "error" for that row |
| `du` not installed | All sizes 0; the `WorktreeSizeResult.error` field is set to `"du not found"`; the popover shows the error in place of the worktree list |
| `git worktree list` fails | `WorktreeSizeResult.error` is set; the summary line is hidden; the popover (if opened via a retry) shows the error |
| Host reports `status: "failed"` | The REST route returns 400 with the error message; `useWorktreeSizes` resolves to an error object; the popover shows the error text when opened |
| Cache is 1 hour old | Cache is still returned (never expires); UI shows "updated 1h ago"; host kicks off background recalc since >10 min elapsed |
| Refresh button clicked | Sends `force=true`; host recalculates immediately (blocks until done); UI shows spinner on the refresh icon; updated data replaces cache |
| First request (no cache) | Host blocks on calculation; UI shows loading spinner; subsequent requests return cache immediately |
| Background recalc in progress | Host returns old cache immediately (non-blocking); updated data arrives on next refetch |

---

## 9. Files to Create / Modify

### New files
| File | Purpose |
|------|---------|
| `omnigent/host/worktree_sizes.py` | Size calculation + cache |
| `web/src/hooks/useWorktreeSizes.ts` | React Query hook |
| `web/src/lib/formatBytes.ts` | Byte formatting utility |

### Modified files
| File | Change |
|------|--------|
| `omnigent/host/frames.py` | Add `WORKTREE_SIZES` / `WORKTREE_SIZES_RESULT` kinds, dataclasses, encode/decode |
| `omnigent/host/daemon_launch.py` (or `_daemon_entry.py`) | Handle `host.worktree_sizes` frame; create `WorktreeSizeCache` at startup |
| `omnigent/server/host_registry.py` | Add `pending_worktree_sizes` dict to `HostConnection`; handle result frame in receive loop |
| `omnigent/server/routes/_host_worktree.py` | Add `worktree_sizes_on_host` proxy |
| `omnigent/server/routes/hosts.py` | Add `GET /hosts/{host_id}/worktree-sizes` route |
| `web/src/shell/NewChatDialog.tsx` | Add summary line + popover below the workspace/auto-worktree row |

---

## 10. Test Plan

- **Unit**: `worktree_sizes.py` — cache TTL, in-flight dedup, `calculate_worktree_sizes` with a temp git repo
- **Unit**: `formatBytes.ts` — 0, 512, 2048, 1048576, 1073741824
- **Integration**: Host frame round-trip — send `host.worktree_sizes`, receive result with correct `total_bytes`
- **E2E (manual)**: Open New Session, select a git repo workspace, verify summary line appears, hover shows underline, click opens popover with per-worktree sizes
- **Performance**: Verify `nice`/`ionice` prefix is applied (check `ps`/`iotop` on Linux); confirm event loop stays responsive during size calculation (serve a concurrent `host.stat` frame while `du` is running)
