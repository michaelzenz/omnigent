import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { authenticatedFetch } from "@/lib/identity";
import { useServerInfo } from "@/lib/CapabilitiesContext";

export interface ModelOption {
  id: string;
  displayName: string;
}

export interface AdminModelSettings {
  databricksConnected: boolean;
  profile: string | null;
  models: ModelOption[];
  omnigentModels: string[];
  policyModel: string | null;
  error: string | null;
}

interface WireModelOption {
  id: string;
  display_name: string;
}

const ADMIN_KEY = ["admin-model-settings"];
let optionsOverride: ModelOption[] | undefined;

async function fetchAdminModelSettings(): Promise<AdminModelSettings> {
  const response = await authenticatedFetch("/v1/admin/model-settings");
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const body = (await response.json()) as {
    databricks_connected: boolean;
    profile: string | null;
    models: WireModelOption[];
    omnigent_models: string[];
    policy_model: string | null;
    error: string | null;
  };
  return {
    databricksConnected: body.databricks_connected,
    profile: body.profile,
    models: body.models.map((model) => ({
      id: model.id,
      displayName: model.display_name,
    })),
    omnigentModels: body.omnigent_models,
    policyModel: body.policy_model,
    error: body.error,
  };
}

export function useOmnigentModelOptions() {
  const info = useServerInfo();
  const advertised = useMemo(
    () =>
      info === "loading" || info.omnigent_model_options === undefined
        ? undefined
        : info.omnigent_model_options.map((model) => ({
            id: model.id,
            displayName: model.display_name,
          })),
    [info],
  );
  return {
    data: optionsOverride ?? advertised,
    isLoading: info === "loading",
    isError: false,
    error: null,
  };
}

export function useAdminModelSettings() {
  return useQuery({
    queryKey: ADMIN_KEY,
    queryFn: fetchAdminModelSettings,
    staleTime: 30_000,
  });
}

export function useUpdateAdminModelSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (patch: { omnigentModels?: string[]; policyModel?: string | null }) => {
      const response = await authenticatedFetch("/v1/admin/model-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(patch.omnigentModels !== undefined ? { omnigent_models: patch.omnigentModels } : {}),
          ...(patch.policyModel !== undefined ? { policy_model: patch.policyModel } : {}),
        }),
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    },
    onSuccess: async (_data, variables) => {
      if (variables.omnigentModels !== undefined) {
        const admin = queryClient.getQueryData<AdminModelSettings>(ADMIN_KEY);
        optionsOverride = variables.omnigentModels.map((id) => ({
          id,
          displayName: admin?.models.find((model) => model.id === id)?.displayName ?? id,
        }));
      }
      await queryClient.invalidateQueries({ queryKey: ADMIN_KEY });
    },
  });
}
