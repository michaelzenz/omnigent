import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { OpenFolder } from "@/lib/events";

const fetchMock = vi.fn();
const desktopOpen = vi.fn();
const browserOpen = vi.fn();
let client: QueryClient;
let folders: OpenFolder[];

const STAMPED: OpenFolder[] = [
  { title: "Repo", path: "/home/me/repo" },
  { title: "Fix worktree", path: "/home/me/repo-fix" },
];
const LABELS_PATH =
  "/v1/sessions/conv_1/resources/environments/default/filesystem/etc/metric_proxy/labels/default?base=host";

function desktop(installed?: { vscode: boolean; cursor: boolean }) {
  vi.stubGlobal("omnigentDesktop", {
    kind: "electron",
    getHostIdentity: async () => ({ cliInstalled: true, hostId: "local" }),
    ...(installed ? { getInstalledEditors: async () => installed } : {}),
  });
}

beforeEach(() => {
  vi.resetModules();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  folders = STAMPED;
  fetchMock.mockReset().mockImplementation(async (input: string) => {
    if (input === "/v1/sessions/conv_1/ui/open-folders") {
      return new Response(JSON.stringify({ folders }));
    }
    if (input === "/v1/hosts") return new Response(JSON.stringify({ hosts: [] }));
    return new Response("", { status: 404 });
  });
  desktopOpen.mockReset();
  browserOpen.mockReset();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    browserOpen(this.href);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("open", desktopOpen);
  desktop({ vscode: true, cursor: true });
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.doUnmock("@/hooks/useHostSshTarget");
  vi.doUnmock("sonner");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function renderMenu(hostId: string | null = "local") {
  const { OpenFoldersMenu } = await import("./OpenFoldersMenu");
  render(
    <QueryClientProvider client={client}>
      <OpenFoldersMenu conversationId="conv_1" hostId={hostId} />
    </QueryClientProvider>,
  );
  await act(() => Promise.resolve());
}

async function openMenu() {
  fireEvent.pointerDown(await screen.findByRole("button", { name: "Open" }), {
    button: 0,
    ctrlKey: false,
  });
}

async function settle() {
  await waitFor(() => expect(client.isFetching()).toBe(0));
  await act(() => Promise.resolve());
}

it("lists the agent's folders, then opens the chosen one in the chosen editor", async () => {
  await renderMenu();
  await settle();
  await openMenu();
  expect(screen.getByRole("menuitem", { name: "Repo" })).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("menuitem", { name: "Fix worktree" }), { key: "ArrowRight" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Cursor" }));
  expect(desktopOpen).toHaveBeenCalledWith(
    "cursor://file/home/me/repo-fix",
    "_blank",
    "noopener,noreferrer",
  );
});

it("opens a folder at once when the OS has only one of the editors", async () => {
  desktop({ vscode: false, cursor: true });
  await renderMenu();
  await settle();
  await openMenu();
  fireEvent.click(screen.getByRole("menuitem", { name: "Repo" }));
  expect(desktopOpen).toHaveBeenCalledWith(
    "cursor://file/home/me/repo",
    "_blank",
    "noopener,noreferrer",
  );
});

it("offers both editors over SSH in a browser, which cannot tell what is installed", async () => {
  vi.stubGlobal("omnigentDesktop", undefined);
  vi.doMock("@/hooks/useHostSshTarget", () => ({
    useHostSshTarget: () => "devbox.ssh",
    useResolveHostSshTarget: () => async () => ({ target: "devbox.ssh" }),
  }));
  await renderMenu("remote");
  await settle();
  await openMenu();
  fireEvent.keyDown(screen.getByRole("menuitem", { name: "Repo" }), { key: "ArrowRight" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "VS Code" }));
  expect(browserOpen).toHaveBeenCalledWith(
    "vscode://vscode-remote/ssh-remote+devbox.ssh/home/me/repo",
  );
});

it("shows the button for an offline host, and a click says the host is not reachable", async () => {
  const toastError = vi.fn();
  vi.doMock("sonner", () => ({ toast: { error: toastError } }));
  await renderMenu("remote");
  await settle();
  await openMenu();
  fireEvent.keyDown(screen.getByRole("menuitem", { name: "Repo" }), { key: "ArrowRight" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Cursor" }));
  await waitFor(() =>
    expect(toastError).toHaveBeenCalledWith("Host not reachable", {
      description: "The session's host is offline. Try again when it reconnects.",
    }),
  );
  expect(desktopOpen).not.toHaveBeenCalled();
});

it("checks a host again on a click after a failed check, then opens the folder over SSH", async () => {
  const route = fetchMock.getMockImplementation()!;
  let labelReads = 0;
  fetchMock.mockImplementation(async (input: string) => {
    if (input === "/v1/hosts") {
      const host = { host_id: "remote", name: "devbox", status: "online", sandbox_provider: null };
      return new Response(JSON.stringify({ hosts: [host] }));
    }
    if (input !== LABELS_PATH) return route(input);
    labelReads += 1;
    if (labelReads === 1) return new Response("", { status: 502 });
    const file = {
      object: "session.environment.filesystem.file_content",
      encoding: "utf-8",
      truncated: false,
      content: "arca_region us-west-2\narca_type arca\n",
    };
    return new Response(JSON.stringify(file));
  });
  await renderMenu("remote");
  await settle();
  await openMenu();
  fireEvent.keyDown(screen.getByRole("menuitem", { name: "Repo" }), { key: "ArrowRight" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Cursor" }));
  await waitFor(() =>
    expect(desktopOpen).toHaveBeenCalledWith(
      "cursor://vscode-remote/ssh-remote+arca.ssh/home/me/repo",
      "_blank",
      "noopener,noreferrer",
    ),
  );
  expect(labelReads).toBe(2);
});

it.each<[string, () => void, string]>([
  [
    "no stamped folders",
    () => {
      folders = [];
    },
    "local",
  ],
  ["no editor app on this OS", () => desktop({ vscode: false, cursor: false }), "local"],
])("stays hidden with %s", async (_case, arrange, hostId) => {
  arrange();
  await renderMenu(hostId);
  await settle();
  expect(screen.queryByRole("button", { name: "Open" })).toBeNull();
});

it("offers both editors in an older shell that cannot detect them", async () => {
  desktop();
  await renderMenu();
  await settle();
  await openMenu();
  fireEvent.keyDown(screen.getByRole("menuitem", { name: "Repo" }), { key: "ArrowRight" });
  expect(await screen.findByRole("menuitem", { name: "VS Code" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "Cursor" })).toBeInTheDocument();
});

it("follows stamps for its session that arrive on the session stream", async () => {
  await renderMenu();
  await settle();
  const { emitUiOpenFolders } = await import("@/lib/openFolders");
  act(() => {
    emitUiOpenFolders(
      { type: "ui_open_folders", folders: [{ title: "Other", path: "/other" }] },
      "conv_2",
    );
    emitUiOpenFolders(
      { type: "ui_open_folders", folders: [{ title: "Docs", path: "/home/me/docs" }] },
      "conv_1",
    );
  });
  await openMenu();
  expect(screen.getByRole("menuitem", { name: "Docs" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Repo" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Other" })).toBeNull();
});
