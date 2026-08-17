import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as modelSettings from "@/hooks/useModelSettings";
import { ModelsPage } from "./ModelsPage";

const mutate = vi.fn();

vi.mock("@/hooks/useModelSettings", () => ({
  useAdminModelSettings: vi.fn(),
  useUpdateAdminModelSettings: vi.fn(),
}));

beforeEach(() => {
  mutate.mockReset();
  vi.mocked(modelSettings.useUpdateAdminModelSettings).mockReturnValue({
    mutate,
    isPending: false,
    isError: false,
    error: null,
  } as never);
});

describe("ModelsPage", () => {
  it("lists discovered models and persists Omnigent toggles", () => {
    vi.mocked(modelSettings.useAdminModelSettings).mockReturnValue({
      data: {
        databricksConnected: true,
        profile: "ai-devtools-prod",
        models: [
          { id: "databricks-gpt-5-4", displayName: "GPT 5 4" },
          { id: "databricks-glm-5-2", displayName: "GLM 5 2" },
        ],
        omnigentModels: ["databricks-glm-5-2"],
        policyModel: null,
        error: null,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as never);

    render(<ModelsPage />);
    expect(screen.getAllByRole("switch").map((item) => item.getAttribute("aria-label"))).toEqual([
      "Offer GLM 5 2 in Omnigent",
      "Offer GPT 5 4 in Omnigent",
    ]);
    fireEvent.click(screen.getByRole("switch", { name: "Offer GPT 5 4 in Omnigent" }));
    expect(mutate).toHaveBeenCalledWith({
      omnigentModels: ["databricks-glm-5-2", "databricks-gpt-5-4"],
    });
  });

  it("asks the admin to connect Databricks when discovery is unavailable", () => {
    vi.mocked(modelSettings.useAdminModelSettings).mockReturnValue({
      data: {
        databricksConnected: false,
        profile: null,
        models: [],
        omnigentModels: [],
        policyModel: null,
        error: null,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    render(<ModelsPage />);
    expect(
      screen.getByText("Connect to a Databricks workspace to discover and configure models."),
    ).toBeInTheDocument();
  });
});
