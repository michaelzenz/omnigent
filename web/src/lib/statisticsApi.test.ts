import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearModelPricing,
  fetchStatisticsReport,
  formatPurposeLabel,
  updateModelPricing,
} from "./statisticsApi";

function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

describe("fetchStatisticsReport", () => {
  it("maps snake_case fields and preserves nullable costs and prices", async () => {
    fetchMock.mockResolvedValueOnce(
      response({
        month: "2026-08",
        available_months: ["2026-07", "2026-08"],
        workload_classification_enabled: true,
        totals: {
          key: "total",
          cost_usd: 0,
          total_tokens: 1200,
          calls: 3,
          priced_calls: 0,
          unpriced_calls: 3,
        },
        daily: [
          {
            key: "2026-08-01",
            cost_usd: 0,
            total_tokens: 1200,
            calls: 3,
            priced_calls: 0,
            unpriced_calls: 3,
          },
        ],
        by_model: [
          {
            key: "model-a",
            cost_usd: 0,
            total_tokens: 1200,
            calls: 3,
            priced_calls: 0,
            unpriced_calls: 3,
          },
        ],
        by_purpose: [
          {
            key: "user_interaction",
            cost_usd: 0,
            total_tokens: 900,
            calls: 2,
            priced_calls: 0,
            unpriced_calls: 2,
          },
          {
            key: "profile_selection+smart_routing+workload_classification",
            cost_usd: 0,
            total_tokens: 300,
            calls: 1,
            priced_calls: 0,
            unpriced_calls: 1,
          },
        ],
        by_workload: [],
        current_pricing: [
          {
            model: "model-a",
            service_pricing_status: "unknown",
            service_input_price_per_token: null,
            service_output_price_per_token: null,
            effective_input_price_per_token: 0.000002,
            effective_output_price_per_token: 0.000004,
            effective_cache_read_price_per_token: null,
            effective_cache_write_price_per_token: 0.000005,
            custom_input_price_per_token: 0.000002,
            custom_output_price_per_token: 0.000004,
            has_custom_pricing: true,
            custom_differs_from_service: true,
          },
        ],
      }),
    );

    const report = await fetchStatisticsReport("2026-08");

    expect(fetchMock.mock.calls[0][0]).toBe("/v1/statistics?month=2026-08");
    expect(report.totals.costUsd).toBeNull();
    expect(report.daily[0].costUsd).toBeNull();
    expect(report.byModel[0].costUsd).toBeNull();
    expect(report.enabledModelPricing[0]).toMatchObject({
      displayName: "model-a",
      inputPerMillion: 2,
      outputPerMillion: 4,
      cacheReadPerMillion: null,
      cacheWritePerMillion: 5,
      servicePricingStatus: "unknown",
      hasCustomPricing: true,
      customDiffersFromService: true,
    });
    expect(report.totals.userTurns).toBe(2);
    expect(report.workloadClassificationEnabled).toBe(true);
    expect(report.byPurpose[1].label).toBe(
      "Profile selection + Smart routing + Workload classification",
    );
  });
});

describe("model pricing mutations", () => {
  it("sends human per-million values to an encoded model path", async () => {
    fetchMock.mockResolvedValueOnce(response({}));

    await updateModelPricing("provider/model name", {
      inputPerMillion: 1.25,
      outputPerMillion: 4,
      cacheReadPerMillion: null,
      cacheWritePerMillion: 5.5,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/statistics/model-pricing/provider%2Fmodel%20name",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          input_price_per_million: 1.25,
          output_price_per_million: 4,
          cache_read_price_per_million: null,
          cache_write_price_per_million: 5.5,
        }),
      }),
    );
  });

  it("clears custom pricing at the encoded model path", async () => {
    fetchMock.mockResolvedValueOnce(response({}));

    await clearModelPricing("provider/model");

    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/statistics/model-pricing/provider%2Fmodel",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

describe("formatPurposeLabel", () => {
  it("keeps a consolidated purpose key as one readable label", () => {
    expect(formatPurposeLabel("smart_routing+workload_classification")).toBe(
      "Smart routing + Workload classification",
    );
  });
});
