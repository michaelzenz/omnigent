import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { StatisticsReport } from "@/lib/statisticsApi";

const {
  statisticsHook,
  settingsHook,
  updateHook,
  updateMutate,
  pricingUpdateHook,
  pricingUpdateMutate,
  pricingClearHook,
  pricingClearMutate,
} = vi.hoisted(() => ({
  statisticsHook: vi.fn(),
  settingsHook: vi.fn(),
  updateHook: vi.fn(),
  updateMutate: vi.fn(),
  pricingUpdateHook: vi.fn(),
  pricingUpdateMutate: vi.fn(),
  pricingClearHook: vi.fn(),
  pricingClearMutate: vi.fn(),
}));

vi.mock("@/hooks/useStatisticsReport", () => ({
  useStatisticsReport: statisticsHook,
  useUpdateModelPricing: pricingUpdateHook,
  useClearModelPricing: pricingClearHook,
}));
vi.mock("@/hooks/useModelSettings", () => ({
  useAdminModelSettings: settingsHook,
  useUpdateAdminModelSettings: updateHook,
}));
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-chart">{children}</div>
  ),
  BarChart: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Bar: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

import { StatisticsPage } from "./StatisticsPage";

function monthKey(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function previousMonth(month: string): string {
  const [year, numericMonth] = month.split("-").map(Number);
  return monthKey(new Date(year, numericMonth - 2, 1));
}

const current = monthKey();
const previous = previousMonth(current);

function reportFor(month = current): StatisticsReport {
  return {
    month,
    availableMonths: [previous, current],
    workloadClassificationEnabled: false,
    totals: {
      costUsd: null,
      totalTokens: 12_500,
      userTurns: 4,
      averageCostPerTurn: null,
    },
    daily: [{ day: `${month}-01`, costUsd: null, totalTokens: 1000, calls: 2 }],
    byModel: [
      {
        key: "model-a",
        label: "Model A",
        costUsd: null,
        totalTokens: 1000,
        calls: 2,
        share: 1,
      },
    ],
    byPurpose: [
      {
        key: "profile_selection+smart_routing+workload_classification",
        label: "Profile selection + Smart routing + Workload classification",
        costUsd: null,
        totalTokens: 1000,
        calls: 1,
        share: 1,
      },
    ],
    byWorkload: [
      {
        key: "development",
        label: "Development",
        costUsd: null,
        totalTokens: 1000,
        calls: 2,
        share: 1,
      },
    ],
    enabledModelPricing: [
      {
        model: "model-a",
        displayName: "Model A",
        inputPerMillion: null,
        outputPerMillion: null,
        cacheReadPerMillion: null,
        cacheWritePerMillion: null,
        servicePricingStatus: "unknown",
        hasCustomPricing: false,
        customDiffersFromService: false,
      },
    ],
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter>
          <StatisticsPage />
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  statisticsHook.mockReset();
  settingsHook.mockReset();
  updateHook.mockReset();
  updateMutate.mockReset();
  pricingUpdateHook.mockReset();
  pricingUpdateMutate.mockReset();
  pricingClearHook.mockReset();
  pricingClearMutate.mockReset();
  statisticsHook.mockImplementation((month: string) => ({
    data: reportFor(month),
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }));
  settingsHook.mockReturnValue({
    data: { workloadClassificationEnabled: false },
    isLoading: false,
    isError: false,
  });
  updateHook.mockReturnValue({ mutate: updateMutate, isPending: false });
  pricingUpdateHook.mockReturnValue({
    mutate: pricingUpdateMutate,
    reset: vi.fn(),
    isPending: false,
    error: null,
  });
  pricingClearHook.mockReturnValue({
    mutate: pricingClearMutate,
    reset: vi.fn(),
    isPending: false,
    error: null,
  });
});

describe("StatisticsPage", () => {
  it("shows the Omnigent scope and all breakdowns simultaneously", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "Omnigent statistics" })).toBeInTheDocument();
    expect(screen.getByText(/OmniHarness only/)).toBeInTheDocument();
    expect(screen.getByTestId("statistics-model-section")).toBeInTheDocument();
    expect(screen.getByTestId("statistics-purpose-section")).toBeInTheDocument();
    expect(screen.getByTestId("statistics-workload-section")).toBeInTheDocument();
    expect(
      screen.getAllByText("Profile selection + Smart routing + Workload classification").length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
  });

  it("defaults to the current month and navigates available months", () => {
    renderPage();

    expect(statisticsHook).toHaveBeenLastCalledWith(current);
    fireEvent.click(screen.getByRole("button", { name: "Previous available month" }));
    expect(statisticsHook).toHaveBeenLastCalledWith(previous);
    fireEvent.click(screen.getByRole("button", { name: "Next available month" }));
    expect(statisticsHook).toHaveBeenLastCalledWith(current);
  });

  it("clearly shows unknown pricing and an edit action", () => {
    renderPage();

    fireEvent.click(screen.getByTestId("statistics-pricing-trigger"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Enabled Omnigent model pricing")).toBeInTheDocument();
    expect(screen.getByText("Service price: Unknown")).toBeInTheDocument();
    expect(screen.getAllByText("Unknown")).toHaveLength(4);
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByText(/affect future calls only/i)).toBeInTheDocument();
  });

  it("edits, cancels, validates, and saves all per-million rates", () => {
    renderPage();
    fireEvent.click(screen.getByTestId("statistics-pricing-trigger"));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(screen.getByLabelText("Input price per 1M for Model A")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Input price per 1M for Model A")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Input rate is required");

    fireEvent.change(screen.getByLabelText("Input price per 1M for Model A"), {
      target: { value: "2" },
    });
    fireEvent.change(screen.getByLabelText("Output price per 1M for Model A"), {
      target: { value: "8" },
    });
    fireEvent.change(screen.getByLabelText("Cache read price per 1M for Model A"), {
      target: { value: "0.2" },
    });
    fireEvent.change(screen.getByLabelText("Cache write price per 1M for Model A"), {
      target: { value: "2.5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(pricingUpdateMutate).toHaveBeenCalledWith(
      {
        model: "model-a",
        pricing: {
          inputPerMillion: 2,
          outputPerMillion: 8,
          cacheReadPerMillion: 0.2,
          cacheWritePerMillion: 2.5,
        },
      },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("resets custom pricing only when it differs from pulled pricing", () => {
    const report = reportFor();
    report.enabledModelPricing[0] = {
      ...report.enabledModelPricing[0],
      inputPerMillion: 2,
      outputPerMillion: 8,
      servicePricingStatus: "known",
      hasCustomPricing: true,
      customDiffersFromService: true,
    };
    statisticsHook.mockReturnValue({
      data: report,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    const view = renderPage();
    fireEvent.click(screen.getByTestId("statistics-pricing-trigger"));
    fireEvent.click(screen.getByRole("button", { name: "Reset to pulled pricing" }));
    expect(pricingClearMutate).toHaveBeenCalledWith("model-a");

    view.unmount();
    report.enabledModelPricing[0].customDiffersFromService = false;
    statisticsHook.mockReturnValue({
      data: report,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    renderPage();
    fireEvent.click(screen.getByTestId("statistics-pricing-trigger"));
    expect(
      screen.queryByRole("button", { name: "Reset to pulled pricing" }),
    ).not.toBeInTheDocument();
  });

  it("shows custom values with unknown service status and allows clearing them", () => {
    const report = reportFor();
    report.enabledModelPricing[0] = {
      ...report.enabledModelPricing[0],
      inputPerMillion: 3,
      outputPerMillion: 9,
      cacheReadPerMillion: 0.3,
      hasCustomPricing: true,
    };
    statisticsHook.mockReturnValue({
      data: report,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    renderPage();
    fireEvent.click(screen.getByTestId("statistics-pricing-trigger"));

    expect(screen.getByText("Service price: Unknown")).toBeInTheDocument();
    expect(screen.getByText("$3.00")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear custom pricing" }));
    expect(pricingClearMutate).toHaveBeenCalledWith("model-a");
  });

  it("disables actions while pending and reports mutation errors", () => {
    pricingUpdateHook.mockReturnValue({
      mutate: pricingUpdateMutate,
      reset: vi.fn(),
      isPending: true,
      error: new Error("failed"),
    });
    renderPage();
    fireEvent.click(screen.getByTestId("statistics-pricing-trigger"));

    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not update pricing");
  });

  it("persists workload monitoring through global model settings", () => {
    renderPage();

    expect(screen.getByText("Monitoring off")).toBeInTheDocument();
    expect(screen.getByText(/historical classifications.*remain visible/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Enable workload monitoring" }));
    expect(updateMutate).toHaveBeenCalledWith({ workloadClassificationEnabled: true });
  });
});
