import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { WatchersSection } from "./WatchersSection";
import type {
  NewWatcher,
  WatchItem,
  WatcherChanges,
  WatcherDetail,
} from "@/hooks/useGlobalHubWatchers";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { updateSession } from "@/lib/sessionsApi";
import type * as SessionsApiModule from "@/lib/sessionsApi";
import { localTimezone } from "@/lib/timezones";
import { jsonResponse } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));
vi.mock("@/lib/sessionsApi", async (importOriginal) => ({
  ...(await importOriginal<typeof SessionsApiModule>()),
  updateSession: vi.fn(),
}));
vi.mock("@/hooks/useGlobalHubSettings", () => ({
  useGlobalHubSettings: () => ({ data: { host_id: "h1", workspace: "/srv" } }),
}));
vi.mock("@/hooks/useHosts", () => ({
  useHostModelOptions: () => ({ data: [{ id: "model-a", displayName: "Model A" }] }),
}));
// Radix Select uses a portal and pointer events that jsdom cannot drive.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value: string;
    onValueChange: (value: string) => void;
    children: ReactNode;
  }) => (
    <select value={value} onChange={(event) => onValueChange(event.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => children,
  SelectItem: ({ value, children }: { value: string; children: ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}));

const fetchMock = vi.mocked(authenticatedFetch);
const WATCHERS = "/v1/agent-tasks/watchers";
const TEMPLATE = "# Watcher manual template";
const EVERY_30_MINUTES = "FREQ=MINUTELY;INTERVAL=30";

function watcher(id: string, fields: Partial<WatcherDetail> = {}): WatcherDetail {
  return {
    id,
    name: "PR watcher",
    description: "",
    model: null,
    customized: [],
    schedule: { enabled: true, rrule: EVERY_30_MINUTES, timezone: "UTC", next_run_at: null },
    session_id: null,
    created_at: 1,
    updated_at: 1,
    builtin: false,
    source_types: [],
    manual: TEMPLATE,
    default_manual: TEMPLATE,
    ...fields,
  };
}

function item(id: string, fields: Partial<WatchItem> = {}): WatchItem {
  return {
    id,
    source: "github",
    source_key: `https://github.com/example-org/repo/pull/${id}`,
    title: `PR ${id}`,
    offsets: { state: "open" },
    status: "active",
    created_at: 1,
    checked_at: Math.floor(Date.now() / 1000) - 5 * 60,
    changed_at: null,
    done_at: null,
    ...fields,
  };
}

/** Serve watchers as the server does: create, change and delete them, and page their items. */
function serveWatchers(saved: WatcherDetail[], items: WatchItem[] = []): void {
  const byId = new Map(saved.map((entry) => [entry.id, entry]));
  fetchMock.mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://test");
    const path = decodeURIComponent(url.pathname);
    const method = init?.method ?? "GET";
    if (path === `${WATCHERS}/defaults`) {
      return jsonResponse({ manual: TEMPLATE, rrule: EVERY_30_MINUTES });
    }
    if (path === WATCHERS && method === "POST") {
      const body = JSON.parse(String(init?.body)) as NewWatcher;
      const created = watcher("w-new", {
        name: body.name,
        manual: body.manual ?? TEMPLATE,
        schedule: { ...body.schedule, next_run_at: null },
      });
      byId.set(created.id, created);
      return jsonResponse(created);
    }
    if (path === WATCHERS) return jsonResponse({ data: [...byId.values()] });
    const [, id, itemsPath] = path.match(/^\/v1\/agent-tasks\/watchers\/([^/]+)(\/items)?$/) ?? [];
    // The session's stop and delete.
    if (id === undefined) return jsonResponse({});
    const found = byId.get(id);
    if (found === undefined) return jsonResponse({ detail: "watcher not found" }, 404);
    if (itemsPath) {
      const listed = items.filter(
        (entry) => url.searchParams.get("status") === "all" || entry.status === "active",
      );
      const after = url.searchParams.get("after");
      const start = after ? listed.findIndex((entry) => entry.id === after) + 1 : 0;
      const end = start + Number(url.searchParams.get("limit"));
      const page = listed.slice(start, end);
      return jsonResponse({
        data: page,
        first_id: page[0]?.id ?? null,
        last_id: page.at(-1)?.id ?? null,
        has_more: end < listed.length,
        total: listed.length,
      });
    }
    if (method === "DELETE") {
      byId.delete(found.id);
      return jsonResponse({ id: found.id, deleted: true, conversation_id: found.session_id });
    }
    if (method === "PATCH") {
      const { manual, schedule, ...fields } = JSON.parse(String(init?.body)) as WatcherChanges;
      const next: WatcherDetail = {
        ...found,
        ...fields,
        ...(manual === undefined
          ? {}
          : { manual: manual ?? TEMPLATE, customized: manual === null ? [] : ["manual" as const] }),
        ...(schedule === undefined ? {} : { schedule: { ...schedule, next_run_at: null } }),
      };
      byId.set(found.id, next);
      return jsonResponse(next);
    }
    return jsonResponse(found);
  });
}

function renderSection(): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WatchersSection />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The bodies of the requests that the section sent with ``method``. */
function bodies(method: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === method)
    .map(([, init]) => JSON.parse(String(init?.body)));
}

/** The paths of the DELETE requests, in order. */
function deletedPaths(): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "DELETE")
    .map(([input]) => String(input));
}

beforeEach(() => {
  vi.mocked(updateSession).mockResolvedValue({} as Awaited<ReturnType<typeof updateSession>>);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WatchersSection", () => {
  it("creates a watcher from the template that runs every 30 minutes", async () => {
    serveWatchers([]);
    renderSection();
    expect(await screen.findByText("No watchers yet.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add watcher" }));
    const form = within(await screen.findByTestId("new-watcher-card"));
    expect(await form.findByLabelText("Manual")).toHaveValue(TEMPLATE);
    expect(form.getByRole("switch", { name: "Schedule" })).toBeChecked();
    expect(form.getByRole("button", { name: "Create watcher" })).toBeDisabled();
    fireEvent.change(form.getByLabelText("Name"), { target: { value: " PR watcher " } });
    fireEvent.click(form.getByRole("button", { name: "Create watcher" }));

    expect(await screen.findByTestId("watcher-card-w-new")).toBeInTheDocument();
    expect(screen.queryByTestId("new-watcher-card")).not.toBeInTheDocument();
    expect(bodies("POST")).toEqual([
      {
        name: "PR watcher",
        description: "",
        model: null,
        manual: null,
        schedule: { enabled: true, rrule: EVERY_30_MINUTES, timezone: localTimezone() },
      },
    ]);
  });

  it("saves the changed fields and switches the running session to a new model", async () => {
    serveWatchers([
      watcher("w1", {
        session_id: "s-w1",
        manual: "Watch the release.",
        customized: ["manual"],
        schedule: null,
      }),
    ]);
    renderSection();
    const card = within(await screen.findByTestId("watcher-card-w1"));
    expect(card.getByRole("link", { name: "Open the PR watcher session" })).toHaveAttribute(
      "href",
      "/c/s-w1",
    );

    fireEvent.click(await card.findByRole("button", { name: "Reset to default" }));
    expect(card.getByLabelText("Manual")).toHaveValue(TEMPLATE);
    fireEvent.change(card.getByLabelText("Name"), { target: { value: "Release watcher" } });
    fireEvent.change(card.getByRole("combobox"), { target: { value: "model-a" } });
    fireEvent.click(card.getByRole("button", { name: "Save" }));

    expect(
      await card.findByText("Saved. 1 running session switched to the new model."),
    ).toBeInTheDocument();
    expect(bodies("PATCH")).toEqual([{ name: "Release watcher", model: "model-a", manual: null }]);
    expect(vi.mocked(updateSession).mock.calls).toEqual([["s-w1", { modelOverride: "model-a" }]]);
  });

  it("deletes a watcher after asking, then its session", async () => {
    serveWatchers([watcher("w1", { session_id: "s-w1" })]);
    renderSection();

    fireEvent.click(await screen.findByRole("button", { name: "Delete watcher PR watcher" }));
    fireEvent.click(await screen.findByTestId("task-confirm-button"));

    await waitFor(() => expect(deletedPaths()).toEqual([`${WATCHERS}/w1`, "/v1/sessions/s-w1"]));
    expect(await screen.findByText("No watchers yet.")).toBeInTheDocument();
  });

  it("marks the built-in fallback watcher, offers no delete for it, and shows each watcher's source types", async () => {
    serveWatchers([
      watcher("w-fallback", {
        name: "Fallback watcher",
        builtin: true,
        source_types: ["jira.issue"],
      }),
      watcher("w1", { source_types: ["github.pull_request"] }),
      watcher("w2", { name: "Slack watcher" }),
    ]);
    renderSection();

    const fallback = within(await screen.findByTestId("watcher-card-w-fallback"));
    expect(fallback.getByText("Built-in")).toBeInTheDocument();
    expect(
      fallback.queryByRole("button", { name: "Delete watcher Fallback watcher" }),
    ).not.toBeInTheDocument();
    const fallbackTypes = within(screen.getByTestId("watcher-source-types-w-fallback"));
    expect(fallbackTypes.getByText("Checks types without a watcher:")).toBeInTheDocument();
    expect(fallbackTypes.getByText("jira.issue")).toBeInTheDocument();

    const assigned = within(screen.getByTestId("watcher-card-w1"));
    expect(assigned.queryByText("Built-in")).not.toBeInTheDocument();
    expect(assigned.getByRole("button", { name: "Delete watcher PR watcher" })).toBeInTheDocument();
    const assignedTypes = within(screen.getByTestId("watcher-source-types-w1"));
    expect(assignedTypes.getByText("Source types:")).toBeInTheDocument();
    expect(assignedTypes.getByText("github.pull_request")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("watcher-source-types-w2")).getByText("none"),
    ).toBeInTheDocument();
  });

  it("pages through the tracked items and shows the done ones on request", async () => {
    const tracked = Array.from({ length: 60 }, (_, index) => item(`i${index + 1}`));
    tracked[0] = item("i1", {
      title: "Fix retries",
      offsets: { state: "open", checks: "passing" },
    });
    const merged = item("i0", { status: "done", title: "Merged PR" });
    serveWatchers([watcher("w1", { schedule: null })], [merged, ...tracked]);
    renderSection();
    const items = within(await screen.findByTestId("watch-items-w1"));

    const first = within(await items.findByTestId("watch-item-i1"));
    expect(first.getByRole("link", { name: "Fix retries" })).toHaveAttribute(
      "href",
      "https://github.com/example-org/repo/pull/i1",
    );
    expect(first.getByText("Checked 5m ago")).toBeInTheDocument();
    expect(first.getByText("checks: passing")).toBeInTheDocument();
    expect(items.getByText("60")).toBeInTheDocument();
    expect(items.queryByTestId("watch-item-i51")).not.toBeInTheDocument();

    fireEvent.click(items.getByRole("button", { name: "Load more" }));
    expect(await items.findByTestId("watch-item-i60")).toBeInTheDocument();
    expect(items.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(items.queryByText("Merged PR")).not.toBeInTheDocument();

    fireEvent.click(items.getByRole("switch", { name: "Show done items" }));
    const done = within(await items.findByTestId("watch-item-i0"));
    expect(done.getByText("Done")).toBeInTheDocument();
  });
});
