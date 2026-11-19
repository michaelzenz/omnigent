import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { SessionSubscription } from "@/hooks/useSessionSubscriptions";

const fetchMock = vi.fn();
let client: QueryClient;
let subscriptions: SessionSubscription[];

const SUBSCRIPTIONS_PATH = "/v1/sessions/conv_1/subscriptions";
const REMOVE_PATH = `${SUBSCRIPTIONS_PATH}/remove`;
const PR = "https://github.com/example-org/repo/pull/12";
const TICKET = "PROJ-12";
const TICKET_PAGE = "https://example.com/browse/PROJ-12";

function subscription(
  id: string,
  sourceType: string,
  sourceKey: string,
  url: string | null = null,
): SessionSubscription {
  return {
    id,
    session_id: "conv_1",
    source_type: sourceType,
    source_key: sourceKey,
    note: null,
    url,
    created_at: 1,
  };
}

beforeEach(() => {
  vi.resetModules();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  subscriptions = [
    subscription("sub_pr", "github.pull_request", PR),
    subscription("sub_ticket", "jira.issue", TICKET),
  ];
  fetchMock.mockReset().mockImplementation(async (input: string, init?: RequestInit) => {
    if (input === SUBSCRIPTIONS_PATH) {
      return new Response(JSON.stringify({ object: "list", data: subscriptions }));
    }
    if (input === REMOVE_PATH && init?.method === "POST") {
      const { source_key } = JSON.parse(String(init.body)) as { source_key: string };
      subscriptions = subscriptions.filter((entry) => entry.source_key !== source_key);
      return new Response(
        JSON.stringify({
          object: "session.subscription",
          session_id: "conv_1",
          source_key,
          removed: true,
        }),
      );
    }
    return new Response("", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.doUnmock("sonner");
  vi.doUnmock("@/lib/clipboard");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function renderMenu(conversationId: string | null = "conv_1") {
  const { WatchingMenu } = await import("./WatchingMenu");
  render(
    <QueryClientProvider client={client}>
      <WatchingMenu conversationId={conversationId} />
    </QueryClientProvider>,
  );
  await act(() => Promise.resolve());
}

async function openMenu() {
  fireEvent.pointerDown(await screen.findByTestId("composer-watching"), {
    button: 0,
    ctrlKey: false,
  });
}

async function settle() {
  await waitFor(() => expect(client.isFetching()).toBe(0));
  await act(() => Promise.resolve());
}

function removedKeys(): unknown[] {
  return fetchMock.mock.calls
    .filter(([input]) => input === REMOVE_PATH)
    .map(([, init]) => JSON.parse(String(init?.body)));
}

it("lists the sources the session follows, then stops watching the chosen one", async () => {
  await renderMenu();
  await settle();
  expect(screen.getByTestId("composer-watching")).toHaveTextContent("Watching 2");

  await openMenu();
  const pullRequest = screen.getByTestId("composer-watching-sub_pr");
  const ticket = screen.getByTestId("composer-watching-sub_ticket");
  expect(pullRequest).toHaveTextContent(PR);
  expect(pullRequest).toHaveTextContent("github.pull_request");
  expect(ticket).toHaveTextContent(TICKET);
  const link = pullRequest.querySelector("a");
  expect([link?.getAttribute("href"), link?.getAttribute("target")]).toEqual([PR, "_blank"]);
  expect(ticket.querySelector("a")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: `Stop watching ${PR}` }));

  await waitFor(() =>
    expect(screen.getByTestId("composer-watching")).toHaveTextContent("Watching 1"),
  );
  expect(removedKeys()).toEqual([{ source_key: PR }]);
  expect(screen.queryByTestId("composer-watching-sub_pr")).toBeNull();
});

it("opens the link of a source whose key is not a web address", async () => {
  subscriptions = [subscription("sub_ticket", "jira.issue", TICKET, TICKET_PAGE)];
  await renderMenu();
  await settle();
  await openMenu();

  const link = screen.getByTestId("composer-watching-sub_ticket").querySelector("a");
  expect([link?.getAttribute("href"), link?.getAttribute("target")]).toEqual([
    TICKET_PAGE,
    "_blank",
  ]);
  expect(link).toHaveTextContent(TICKET);
});

it("copies a source key that is not a web address", async () => {
  const copyText = vi.fn(async () => {});
  const toastSuccess = vi.fn();
  vi.doMock("@/lib/clipboard", () => ({ copyText }));
  vi.doMock("sonner", () => ({ toast: { success: toastSuccess, error: vi.fn() } }));
  await renderMenu();
  await settle();
  await openMenu();

  fireEvent.click(screen.getByText(TICKET));

  await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith(`Copied ${TICKET}`));
  expect(copyText).toHaveBeenCalledWith(TICKET);
});

it("stays hidden while the session follows nothing", async () => {
  subscriptions = [];
  await renderMenu();
  await settle();
  expect(fetchMock).toHaveBeenCalledWith(SUBSCRIPTIONS_PATH, expect.anything());
  expect(screen.queryByTestId("composer-watching")).toBeNull();
});

it("asks for nothing without a session", async () => {
  await renderMenu(null);
  await settle();
  expect(fetchMock).not.toHaveBeenCalled();
  expect(screen.queryByTestId("composer-watching")).toBeNull();
});

it("says so when the server refuses to stop watching a source", async () => {
  const toastError = vi.fn();
  vi.doMock("sonner", () => ({ toast: { error: toastError } }));
  const route = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (input: string, init?: RequestInit) => {
    if (input === REMOVE_PATH) {
      return new Response(
        JSON.stringify({ error: { code: "forbidden", message: "needs edit permission" } }),
        { status: 403 },
      );
    }
    return route(input, init);
  });
  await renderMenu();
  await settle();
  await openMenu();
  fireEvent.click(screen.getByRole("button", { name: `Stop watching ${TICKET}` }));

  await waitFor(() =>
    expect(toastError).toHaveBeenCalledWith("Could not stop watching", {
      description: "needs edit permission",
    }),
  );
  expect(screen.getByTestId("composer-watching")).toHaveTextContent("Watching 2");
});
