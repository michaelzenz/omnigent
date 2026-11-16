import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { useAgentManagers } from "./useGlobalHubTasks";

const fetchMock = vi.fn();

function manager(id: string) {
  return { id, title: id, conversation_id: null, session_missing: false };
}

function renderManagers() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderHook(() => useAgentManagers(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

it("reads every page of managers, full ones included", async () => {
  fetchMock.mockImplementation(async (input: string) => {
    const query = new URL(input, "http://board.example.com").searchParams;
    expect(query.get("include_full")).toBe("true");
    const page =
      query.get("after") === "m2"
        ? { data: [manager("m3")], has_more: false, last_id: "m3" }
        : { data: [manager("m1"), manager("m2")], has_more: true, last_id: "m2" };
    return new Response(JSON.stringify(page));
  });
  vi.stubGlobal("fetch", fetchMock);

  const { result } = renderManagers();

  await waitFor(() =>
    expect(result.current.data?.map((entry) => entry.id)).toEqual(["m1", "m2", "m3"]),
  );
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("reads a server without pages in one request", async () => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ data: [manager("m1")] })));
  vi.stubGlobal("fetch", fetchMock);

  const { result } = renderManagers();

  await waitFor(() => expect(result.current.data?.map((entry) => entry.id)).toEqual(["m1"]));
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
