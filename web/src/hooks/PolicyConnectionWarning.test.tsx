import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { showToast } from "@/components/ui/toast";
import { useDefaultPolicies } from "@/hooks/useDefaultPolicies";
import { useIsAdmin } from "@/hooks/useIsAdmin";
import { useAdminModelSettings } from "@/hooks/useModelSettings";
import { usePolicyRegistry } from "@/hooks/usePolicies";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { PolicyConnectionWarning } from "./PolicyConnectionWarning";

vi.mock("@/components/ui/toast", () => ({ showToast: vi.fn() }));
vi.mock("@/hooks/useDefaultPolicies", () => ({ useDefaultPolicies: vi.fn() }));
vi.mock("@/hooks/useIsAdmin", () => ({ useIsAdmin: vi.fn() }));
vi.mock("@/hooks/useModelSettings", () => ({ useAdminModelSettings: vi.fn() }));
vi.mock("@/hooks/usePolicies", () => ({ usePolicyRegistry: vi.fn() }));
vi.mock("@/lib/CapabilitiesContext", () => ({ useServerInfo: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useIsAdmin).mockReturnValue(true);
  vi.mocked(useServerInfo).mockReturnValue("loading");
  vi.mocked(useDefaultPolicies).mockReturnValue({
    data: [
      {
        id: "policy_1",
        object: "default_policy",
        name: "Intent check",
        type: "python",
        handler: "test.intent",
        enabled: true,
        created_at: 1,
        updated_at: null,
        created_by: null,
      },
    ],
    isSuccess: true,
  } as never);
  vi.mocked(usePolicyRegistry).mockReturnValue({
    data: [
      {
        handler: "test.intent",
        kind: "factory",
        name: "Intent check",
        description: "Uses AI.",
        params_schema: null,
        requires_llm: true,
      },
    ],
    isSuccess: true,
  } as never);
  vi.mocked(useAdminModelSettings).mockReturnValue({
    data: {
      databricksConnected: false,
      profile: null,
      models: [],
      omniharnessModels: [],
      policyModel: "databricks-test",
      error: null,
    },
    isSuccess: true,
  } as never);
});

describe("PolicyConnectionWarning", () => {
  it("shows one persistent warning for an enabled AI-backed policy without Databricks", async () => {
    const { rerender } = render(<PolicyConnectionWarning />);
    rerender(<PolicyConnectionWarning />);

    await waitFor(() => expect(showToast).toHaveBeenCalledTimes(1));
    expect(vi.mocked(showToast).mock.calls[0]?.[1]).toEqual({ duration: 0 });
  });

  it("does not warn when Databricks is connected", () => {
    vi.mocked(useAdminModelSettings).mockReturnValue({
      data: {
        databricksConnected: true,
        profile: "test",
        models: [],
        omniharnessModels: [],
        policyModel: "databricks-test",
        error: null,
      },
      isSuccess: true,
    } as never);

    render(<PolicyConnectionWarning />);
    expect(showToast).not.toHaveBeenCalled();
  });

  it("does not warn when no enabled policy requires AI", () => {
    vi.mocked(useDefaultPolicies).mockReturnValue({
      data: [
        {
          id: "policy_1",
          object: "default_policy",
          name: "Intent check",
          type: "python",
          handler: "test.intent",
          enabled: false,
          created_at: 1,
          updated_at: null,
          created_by: null,
        },
      ],
      isSuccess: true,
    } as never);

    render(<PolicyConnectionWarning />);
    expect(showToast).not.toHaveBeenCalled();
  });

  it("does not request admin settings for a non-admin deployment user", () => {
    vi.mocked(useIsAdmin).mockReturnValue(false);

    render(<PolicyConnectionWarning />);
    expect(useAdminModelSettings).toHaveBeenCalledWith(false);
    expect(showToast).not.toHaveBeenCalled();
  });
});
