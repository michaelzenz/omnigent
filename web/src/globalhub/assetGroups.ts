import type { AgentTaskAsset } from "@/hooks/useGlobalHubTasks";

// The common categories in the manager manual, in v2's order.
const COMMON_CATEGORIES: ReadonlyMap<string, string> = new Map([
  ["code", "Code"],
  ["tests", "Tests"],
  ["documents", "Documents"],
  ["data_science", "Data science"],
  ["tables", "Tables"],
  ["logs", "Logs"],
]);
const OTHER_CATEGORY = "other";

/** The assets in one category, with the category's heading. */
export interface AssetGroup {
  category: string;
  label: string;
  assets: AgentTaskAsset[];
}

/**
 * Group a task's assets by category: the common categories first, then the
 * others from A to Z, then "other". Each group keeps the assets' order.
 */
export function groupAssets(assets: readonly AgentTaskAsset[]): AssetGroup[] {
  const byCategory = new Map<string, AgentTaskAsset[]>();
  for (const asset of assets) {
    byCategory.set(asset.category, [...(byCategory.get(asset.category) ?? []), asset]);
  }
  const common = [...COMMON_CATEGORIES.keys()].filter((category) => byCategory.has(category));
  const custom = [...byCategory.keys()]
    .filter((category) => !COMMON_CATEGORIES.has(category) && category !== OTHER_CATEGORY)
    .sort((a, b) => a.localeCompare(b));
  const other = byCategory.has(OTHER_CATEGORY) ? [OTHER_CATEGORY] : [];
  return [...common, ...custom, ...other].map((category) => ({
    category,
    label: COMMON_CATEGORIES.get(category) ?? (category === OTHER_CATEGORY ? "Other" : category),
    assets: byCategory.get(category) ?? [],
  }));
}
