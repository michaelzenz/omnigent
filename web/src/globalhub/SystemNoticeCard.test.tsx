import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SystemNoticeCard } from "./SystemNoticeCard";
import type * as NoticeBridgeModule from "./noticeBridge";
import type { SystemNotice } from "@/hooks/useGlobalHubNotice";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { HTML_PREVIEW_SANDBOX } from "@/shell/codeViewerHelpers";
import { jsonResponse, noticeInput, systemNotice } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

// jsdom does not run the frame's script, so the test relays the frame's clicks.
const frame = vi.hoisted(() => ({
  relay: null as null | ((actionId: string, inputs: Record<string, boolean | string>) => void),
}));
vi.mock("./noticeBridge", async (importOriginal) => ({
  ...(await importOriginal<typeof NoticeBridgeModule>()),
  connectNoticeFrame: (
    _window: Window,
    _nonce: string,
    isDeclared: (actionId: string) => boolean,
    onAction: (actionId: string, inputs: Record<string, boolean | string>) => void,
  ) => {
    frame.relay = (actionId, inputs) => {
      if (isDeclared(actionId)) onAction(actionId, inputs);
    };
    return { close: () => {} } as unknown as MessagePort;
  },
}));

const fetchMock = vi.mocked(authenticatedFetch);
const NOTICE_PATH = "/v1/agent-tasks/system-notice";

/** Serve ``notice`` as the active notice; a click gets ``clickResponse``. */
function serveNotice(
  notice: SystemNotice,
  clickResponse: () => Response = () => jsonResponse({}),
): void {
  fetchMock.mockImplementation(async (input) => {
    const path = String(input);
    if (path === NOTICE_PATH) return jsonResponse({ notice });
    if (path.startsWith(`${NOTICE_PATH}/actions/`)) return clickResponse();
    return jsonResponse({ notice: null });
  });
}

function renderCard(): void {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SystemNoticeCard />
    </QueryClientProvider>,
  );
}

/** The path and parsed body of each POST the card sent. */
function posts(): [string, unknown][] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([input, init]) => [String(input), JSON.parse(String(init?.body))]);
}

function switchState(name: string): string | null {
  return screen.getByRole("switch", { name }).getAttribute("aria-checked");
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SystemNoticeCard", () => {
  it("shows the notice with its publisher and body, and marks it seen", async () => {
    serveNotice(
      systemNotice("n1", {
        title: "3 stale sessions",
        publisher_label: "Custodian",
        body: "Toggle the **ones** to delete.",
      }),
    );

    renderCard();

    expect(await screen.findByRole("heading", { name: "3 stale sessions" })).toBeInTheDocument();
    expect(screen.getByText("Published by Custodian")).toBeInTheDocument();
    expect(screen.getByText("ones")).toBeInTheDocument();
    expect(screen.queryByText(/\*\*/)).toBeNull();
    expect(localStorage.getItem("omnigent.globalhub.seen-notice-id")).toBe("n1");
  });

  it("switches a group's toggles, or all of an action's, together", async () => {
    serveNotice(
      systemNotice("n2", {
        actions: [
          {
            id: "confirm_cleanup",
            label: "Delete selected sessions",
            inputs: [
              noticeInput("delete_a1", { label: "Fix retries", group: "host-a" }),
              noticeInput("delete_a2", { label: "Read logs", group: "host-a" }),
              noticeInput("delete_b1", { label: "Draft doc", group: "host-b" }),
            ],
          },
        ],
      }),
    );
    renderCard();
    const states = () =>
      ["Fix retries", "Read logs", "Draft doc", "All of Delete selected sessions"].map(switchState);

    fireEvent.click(await screen.findByRole("switch", { name: "All in host-a" }));
    const afterGroup = states();
    fireEvent.click(screen.getByRole("switch", { name: "All of Delete selected sessions" }));

    expect([afterGroup, states()]).toEqual([
      ["true", "true", "false", "false"],
      ["true", "true", "true", "true"],
    ]);
    // A group with one toggle needs no switch of its own.
    expect(screen.queryByRole("switch", { name: "All in host-b" })).toBeNull();
  });

  it("sends the form's values, then says so and keeps the button disabled", async () => {
    serveNotice(
      systemNotice("n3", {
        actions: [
          {
            id: "confirm",
            label: "Delete selected",
            inputs: [
              noticeInput("delete_s1", { label: "Fix retries", default: true }),
              noticeInput("note", { kind: "text", label: "Instructions" }),
            ],
          },
        ],
      }),
    );
    renderCard();

    fireEvent.change(await screen.findByRole("textbox", { name: "Instructions" }), {
      target: { value: "keep the branch" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));

    expect(await screen.findByText("Sent to the custodian.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete selected" })).toBeDisabled();
    expect(posts()).toEqual([
      [
        `${NOTICE_PATH}/actions/confirm/click`,
        { inputs: { delete_s1: true, note: "keep the branch" } },
      ],
    ]);
  });

  it("shows why a click failed and lets the user try again", async () => {
    serveNotice(
      systemNotice("n4", {
        actions: [
          {
            id: "confirm",
            label: "Delete selected",
            inputs: [noticeInput("note", { kind: "text", label: "Instructions", required: true })],
          },
        ],
      }),
      () => jsonResponse({ error: { message: "input 'note' is required" } }, 400),
    );
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: "Delete selected" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("input 'note' is required");
    expect(screen.getByRole("button", { name: "Delete selected" })).toBeEnabled();
    expect(screen.getByText("Instructions *")).toBeInTheDocument();
  });

  it("runs an HTML notice in a sandboxed frame and sends each declared action once", async () => {
    serveNotice(
      systemNotice("n6", {
        body_kind: "html",
        body: "<button data-action='confirm'>Go</button>",
        actions: [{ id: "confirm", label: "Delete selected", inputs: [noticeInput("delete_s1")] }],
      }),
    );
    renderCard();

    const notice = await screen.findByTitle("System notice");
    fireEvent.load(notice);
    act(() => {
      frame.relay?.("undeclared", {});
      frame.relay?.("confirm", { delete_s1: true });
      frame.relay?.("confirm", { delete_s1: true });
    });

    expect(await screen.findByText("Sent to the custodian: Delete selected.")).toBeInTheDocument();
    expect(notice).toHaveAttribute("sandbox", HTML_PREVIEW_SANDBOX);
    expect(notice.getAttribute("srcdoc")).toContain("data-action='confirm'");
    expect(screen.queryByRole("button", { name: "Delete selected" })).toBeNull();
    expect(posts()).toEqual([
      [`${NOTICE_PATH}/actions/confirm/click`, { inputs: { delete_s1: true } }],
    ]);
  });

  it("dismisses the notice", async () => {
    serveNotice(systemNotice("n5"));
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: "Dismiss system notice" }));

    await waitFor(() => expect(posts()).toEqual([[`${NOTICE_PATH}/dismiss`, {}]]));
  });
});
