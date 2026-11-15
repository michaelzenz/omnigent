import { describe, expect, it } from "vitest";

import { groupAssets } from "./assetGroups";
import { taskAsset } from "@/test/globalHubFixtures";

describe("groupAssets", () => {
  it("puts the common categories first, the others from A to Z, then other", () => {
    const groups = groupAssets([
      taskAsset("a1", { category: "other" }),
      taskAsset("a2", { category: "runbooks" }),
      taskAsset("a3", { category: "documents" }),
      taskAsset("a4", { category: "code" }),
      taskAsset("a5", { category: "dashboards" }),
      taskAsset("a6", { category: "documents" }),
    ]);

    expect(groups.map(({ label, assets }) => [label, assets.map((asset) => asset.id)])).toEqual([
      ["Code", ["a4"]],
      ["Documents", ["a3", "a6"]],
      ["dashboards", ["a5"]],
      ["runbooks", ["a2"]],
      ["Other", ["a1"]],
    ]);
  });
});
