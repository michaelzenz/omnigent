import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAdminModelSettings, useUpdateAdminModelSettings } from "./useModelSettings";

function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
  } as Response;
}

const fetchMock = vi.fn();
let queryClient: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  fetchMock.mockReset();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

describe("workload classification model setting", () => {
  it("reads the server boolean and persists updates through the admin endpoint", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          databricks_connected: true,
          profile: "test",
          models: [],
          omniharness_models: [],
          workload_classification_enabled: true,
          policy_model: null,
          smart_routing_decision_model: null,
          smart_routing_prompt: null,
          smart_routing_cadence: "first_turn_only",
          error: null,
        }),
      )
      .mockResolvedValueOnce(response({}))
      .mockResolvedValueOnce(
        response({
          databricks_connected: true,
          profile: "test",
          models: [],
          omniharness_models: [],
          workload_classification_enabled: false,
          policy_model: null,
          smart_routing_decision_model: null,
          smart_routing_prompt: null,
          smart_routing_cadence: "first_turn_only",
          error: null,
        }),
      );

    const { result } = renderHook(
      () => ({
        settings: useAdminModelSettings(),
        update: useUpdateAdminModelSettings(),
      }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.settings.data).toBeDefined());
    expect(result.current.settings.data?.workloadClassificationEnabled).toBe(true);

    await act(async () => {
      await result.current.update.mutateAsync({ workloadClassificationEnabled: false });
    });

    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/v1/admin/model-settings");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ workload_classification_enabled: false });
  });
});
