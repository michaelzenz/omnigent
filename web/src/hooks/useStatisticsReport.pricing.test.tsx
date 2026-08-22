import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { updateModelPricing, clearModelPricing } = vi.hoisted(() => ({
  updateModelPricing: vi.fn(),
  clearModelPricing: vi.fn(),
}));

vi.mock("@/lib/statisticsApi", () => ({
  updateModelPricing,
  clearModelPricing,
}));

import { useClearModelPricing, useUpdateModelPricing } from "./useStatisticsReport";

let queryClient: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  updateModelPricing.mockReset().mockResolvedValue(undefined);
  clearModelPricing.mockReset().mockResolvedValue(undefined);
});

describe("statistics pricing mutations", () => {
  it("invalidates the displayed statistics month after save", async () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
    const { result } = renderHook(() => useUpdateModelPricing("2026-08"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        model: "model-a",
        pricing: {
          inputPerMillion: 2,
          outputPerMillion: 8,
          cacheReadPerMillion: null,
          cacheWritePerMillion: null,
        },
      });
    });

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["statistics", "2026-08"] });
  });

  it("invalidates the displayed statistics month after clear", async () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
    const { result } = renderHook(() => useClearModelPricing("2026-08"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync("model-a");
    });

    expect(clearModelPricing.mock.calls[0]?.[0]).toBe("model-a");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["statistics", "2026-08"] });
  });
});
