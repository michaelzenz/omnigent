import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useUpdatePolicy } from "./usePolicies";

const fetchMock = vi.fn();

function wrapperWith(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useUpdatePolicy", () => {
  it("PATCHes editable fields and invalidates the session policy list", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: "p1" }),
    } as Response);
    const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useUpdatePolicy("conv 1"), {
      wrapper: wrapperWith(queryClient),
    });

    result.current.mutate({
      policyId: "policy/1",
      name: "renamed",
      factory_params: { threshold: 9 },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/sessions/conv%201/policies/policy%2F1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({
      name: "renamed",
      factory_params: { threshold: 9 },
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["policies", "conv 1"] });
  });
});
