import { authenticatedFetch } from "./identity";

interface StatisticsBucketWire {
  key: string;
  cost_usd: number;
  total_tokens: number;
  calls: number;
  priced_calls: number;
  unpriced_calls: number;
}

interface EnabledModelPricingWire {
  model: string;
  pricing_status?: string;
  service_pricing_status?: string;
  input_price_per_token?: number | null;
  output_price_per_token?: number | null;
  cache_read_price_per_token?: number | null;
  cache_write_price_per_token?: number | null;
  service_input_price_per_token?: number | null;
  service_output_price_per_token?: number | null;
  service_cache_read_price_per_token?: number | null;
  service_cache_write_price_per_token?: number | null;
  custom_input_price_per_token?: number | null;
  custom_output_price_per_token?: number | null;
  custom_cache_read_price_per_token?: number | null;
  custom_cache_write_price_per_token?: number | null;
  effective_input_price_per_token?: number | null;
  effective_output_price_per_token?: number | null;
  effective_cache_read_price_per_token?: number | null;
  effective_cache_write_price_per_token?: number | null;
  has_custom_pricing?: boolean;
  custom_differs_from_service?: boolean;
}

interface StatisticsReportWire {
  month: string;
  available_months: string[];
  workload_classification_enabled: boolean;
  totals: StatisticsBucketWire;
  daily: StatisticsBucketWire[];
  by_model: StatisticsBucketWire[];
  by_purpose: StatisticsBucketWire[];
  by_workload: StatisticsBucketWire[];
  current_pricing: EnabledModelPricingWire[];
}

export interface StatisticsBreakdown {
  key: string;
  label: string;
  costUsd: number | null;
  totalTokens: number;
  calls: number;
  share: number;
}

export interface EnabledModelPricing {
  model: string;
  displayName: string;
  inputPerMillion: number | null;
  outputPerMillion: number | null;
  cacheReadPerMillion: number | null;
  cacheWritePerMillion: number | null;
  servicePricingStatus: string;
  hasCustomPricing: boolean;
  customDiffersFromService: boolean;
}

export interface ModelPricingInput {
  inputPerMillion: number;
  outputPerMillion: number;
  cacheReadPerMillion: number | null;
  cacheWritePerMillion: number | null;
}

export interface StatisticsReport {
  month: string;
  availableMonths: string[];
  totals: {
    costUsd: number | null;
    totalTokens: number;
    userTurns: number;
    averageCostPerTurn: number | null;
  };
  daily: {
    day: string;
    costUsd: number | null;
    totalTokens: number;
    calls: number;
  }[];
  byModel: StatisticsBreakdown[];
  byPurpose: StatisticsBreakdown[];
  byWorkload: StatisticsBreakdown[];
  enabledModelPricing: EnabledModelPricing[];
  workloadClassificationEnabled: boolean;
}

function knownCost(row: StatisticsBucketWire): number | null {
  return row.priced_calls > 0 ? row.cost_usd : null;
}

function displayLabel(key: string): string {
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function firstDefined<T>(...values: (T | undefined)[]): T | undefined {
  return values.find((value) => value !== undefined);
}

function perMillion(value: number | null | undefined): number | null {
  return value == null ? null : value * 1_000_000;
}

function mapPricing(row: EnabledModelPricingWire): EnabledModelPricing {
  return {
    model: row.model,
    displayName: row.model,
    inputPerMillion: perMillion(
      firstDefined(row.effective_input_price_per_token, row.input_price_per_token),
    ),
    outputPerMillion: perMillion(
      firstDefined(row.effective_output_price_per_token, row.output_price_per_token),
    ),
    cacheReadPerMillion: perMillion(
      firstDefined(row.effective_cache_read_price_per_token, row.cache_read_price_per_token),
    ),
    cacheWritePerMillion: perMillion(
      firstDefined(row.effective_cache_write_price_per_token, row.cache_write_price_per_token),
    ),
    servicePricingStatus: firstDefined(row.service_pricing_status, row.pricing_status) ?? "unknown",
    hasCustomPricing:
      row.has_custom_pricing ??
      (row.custom_input_price_per_token != null && row.custom_output_price_per_token != null),
    customDiffersFromService: row.custom_differs_from_service === true,
  };
}

function mapBreakdown(row: StatisticsBucketWire, totalKnownCost: number): StatisticsBreakdown {
  const costUsd = knownCost(row);
  return {
    key: row.key,
    label: displayLabel(row.key),
    costUsd,
    totalTokens: row.total_tokens,
    calls: row.calls,
    share: costUsd != null && totalKnownCost > 0 ? costUsd / totalKnownCost : 0,
  };
}

export function formatPurposeLabel(key: string, fallbackLabel?: string): string {
  if (!key.includes("_") && !key.includes("+")) return fallbackLabel || key;
  return key
    .split("+")
    .map((purpose) => {
      const words = purpose.replaceAll("_", " ");
      return words.charAt(0).toUpperCase() + words.slice(1);
    })
    .join(" + ");
}

export async function fetchStatisticsReport(month: string): Promise<StatisticsReport> {
  const response = await authenticatedFetch(`/v1/statistics?month=${encodeURIComponent(month)}`);
  if (!response.ok) throw new Error(`Statistics fetch failed: ${response.status}`);

  const wire: StatisticsReportWire = await response.json();
  const totalCostUsd = knownCost(wire.totals);
  const userTurns =
    (wire.by_purpose ?? []).find((row) => row.key === "user_interaction")?.calls ?? 0;
  const totalKnownCost = totalCostUsd ?? 0;
  return {
    month: wire.month,
    availableMonths: wire.available_months ?? [],
    workloadClassificationEnabled: wire.workload_classification_enabled === true,
    totals: {
      costUsd: totalCostUsd,
      totalTokens: wire.totals.total_tokens,
      userTurns,
      averageCostPerTurn: totalCostUsd != null && userTurns > 0 ? totalCostUsd / userTurns : null,
    },
    daily: (wire.daily ?? []).map((row) => ({
      day: row.key,
      costUsd: knownCost(row),
      totalTokens: row.total_tokens,
      calls: row.calls,
    })),
    byModel: (wire.by_model ?? []).map((row) => mapBreakdown(row, totalKnownCost)),
    byPurpose: (wire.by_purpose ?? []).map((row) => ({
      ...mapBreakdown(row, totalKnownCost),
      label: formatPurposeLabel(row.key),
    })),
    byWorkload: (wire.by_workload ?? []).map((row) => mapBreakdown(row, totalKnownCost)),
    enabledModelPricing: (wire.current_pricing ?? []).map(mapPricing),
  };
}

export async function updateModelPricing(model: string, pricing: ModelPricingInput): Promise<void> {
  const response = await authenticatedFetch(
    `/v1/statistics/model-pricing/${encodeURIComponent(model)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input_price_per_million: pricing.inputPerMillion,
        output_price_per_million: pricing.outputPerMillion,
        cache_read_price_per_million: pricing.cacheReadPerMillion,
        cache_write_price_per_million: pricing.cacheWritePerMillion,
      }),
    },
  );
  if (!response.ok) throw new Error(`Model pricing update failed: ${response.status}`);
}

export async function clearModelPricing(model: string): Promise<void> {
  const response = await authenticatedFetch(
    `/v1/statistics/model-pricing/${encodeURIComponent(model)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error(`Model pricing clear failed: ${response.status}`);
}
