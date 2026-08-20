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
        omniharnessModels: ["databricks-glm-5-2"],
        policyModel: null,
        smartRoutingDecisionModel: "databricks-gpt-5-4",
        smartRoutingPrompt: "",
        smartRoutingCadence: "per_turn",
        error: null,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as never);

    render(<ModelsPage />);
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent),
    ).toEqual(["Smart Routing", "OmniHarness"]);
    expect(screen.getAllByRole("switch").map((item) => item.getAttribute("aria-label"))).toEqual([
      "Route every Omnigent turn",
      "Offer GLM 5 2 in Omnigent",
      "Offer GPT 5 4 in Omnigent",
    ]);
    fireEvent.click(screen.getByRole("switch", { name: "Offer GPT 5 4 in Omnigent" }));
    expect(mutate).toHaveBeenCalledWith({
      omniharnessModels: ["databricks-glm-5-2", "databricks-gpt-5-4"],
    });
  });

  it("asks the admin to connect Databricks when discovery is unavailable", () => {
    vi.mocked(modelSettings.useAdminModelSettings).mockReturnValue({
      data: {
        databricksConnected: false,
        profile: null,
        models: [],
        omniharnessModels: [],
        policyModel: null,
        smartRoutingDecisionModel: "databricks-gpt-5-6-luna",
        smartRoutingPrompt: "",
        smartRoutingCadence: "per_turn",
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

  it("persists Smart Routing cadence and custom guidance", () => {
    vi.mocked(modelSettings.useAdminModelSettings).mockReturnValue({
      data: {
        databricksConnected: true,
        profile: "ai-devtools-prod",
        models: [{ id: "databricks-gpt-5-6-luna", displayName: "GPT 5.6 Luna" }],
        omniharnessModels: [],
        policyModel: null,
        smartRoutingDecisionModel: "databricks-gpt-5-6-luna",
        smartRoutingPrompt: "",
        smartRoutingCadence: "per_turn",
        error: null,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as never);

    render(<ModelsPage />);
    expect(screen.getByText(/Global routing settings for OmniHarness only/)).toBeVisible();

    fireEvent.click(screen.getByRole("switch", { name: "Route every Omnigent turn" }));
    expect(mutate).toHaveBeenCalledWith({ smartRoutingCadence: "first_turn_only" });

    fireEvent.change(screen.getByTestId("smart-routing-prompt"), {
      target: { value: "Prefer low-latency models." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save guidance" }));
    expect(mutate).toHaveBeenCalledWith({
      smartRoutingPrompt: "Prefer low-latency models.",
    });
  });
});
