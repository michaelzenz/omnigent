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
  omniharnessModels: string[];
  workloadClassificationEnabled?: boolean;
  workloadCustomCategories?: string[];
  policyModel: string | null;
  smartRoutingDecisionModel: string | null;
  smartRoutingPrompt: string | null;
  smartRoutingCadence: "per_turn" | "first_turn_only";
  turnSelectionUserMessageCount: number;
  error: string | null;
}

interface WireModelOption {
  id: string;
  display_name: string;
}

const ADMIN_KEY = ["admin-model-settings"];
const OMNIHARNESS_SETTINGS_KEY = ["omniharness-settings"];
let optionsOverride: ModelOption[] | undefined;

async function fetchAdminModelSettings(): Promise<AdminModelSettings> {
  const response = await authenticatedFetch("/v1/admin/model-settings");
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const body = (await response.json()) as {
    databricks_connected: boolean;
    profile: string | null;
    models: WireModelOption[];
    omniharness_models: string[];
    workload_classification_enabled?: boolean;
    workload_custom_categories?: string[];
    policy_model: string | null;
    smart_routing_decision_model: string | null;
    smart_routing_prompt: string | null;
    smart_routing_cadence: "per_turn" | "first_turn_only";
    turn_selection_user_message_count: number;
    error: string | null;
  };
  return {
    databricksConnected: body.databricks_connected,
    profile: body.profile,
    models: body.models.map((model) => ({
      id: model.id,
      displayName: model.display_name,
    })),
    omniharnessModels: body.omniharness_models,
    workloadClassificationEnabled: body.workload_classification_enabled === true,
    workloadCustomCategories: body.workload_custom_categories ?? [],
    policyModel: body.policy_model,
    smartRoutingDecisionModel: body.smart_routing_decision_model,
    smartRoutingPrompt: body.smart_routing_prompt,
    smartRoutingCadence: body.smart_routing_cadence,
    turnSelectionUserMessageCount: body.turn_selection_user_message_count,
    error: body.error,
  };
}

export function useOmniHarnessModelOptions() {
  const info = useServerInfo();
  const advertised = useMemo(
    () =>
      info === "loading" || info.omniharness_model_options === undefined
        ? undefined
        : info.omniharness_model_options.map((model) => ({
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

export function useOmniHarnessSettings(enabled = true) {
  return useQuery({
    queryKey: OMNIHARNESS_SETTINGS_KEY,
    queryFn: async () => {
      const response = await authenticatedFetch("/v1/omniharness/settings");
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const body = (await response.json()) as {
        system_prompt: string;
        prompt_profile_auto_include_limit: number;
      };
      return {
        systemPrompt: body.system_prompt,
        promptProfileAutoIncludeLimit: body.prompt_profile_auto_include_limit,
      };
    },
    staleTime: 30_000,
    enabled,
  });
}

export function useUpdateOmniHarnessSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      systemPrompt,
      promptProfileAutoIncludeLimit,
    }: {
      systemPrompt?: string;
      promptProfileAutoIncludeLimit?: number;
    }) => {
      const response = await authenticatedFetch("/v1/omniharness/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(systemPrompt !== undefined ? { system_prompt: systemPrompt } : {}),
          ...(promptProfileAutoIncludeLimit !== undefined
            ? { prompt_profile_auto_include_limit: promptProfileAutoIncludeLimit }
            : {}),
        }),
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const body = (await response.json()) as {
        system_prompt: string;
        prompt_profile_auto_include_limit: number;
      };
      return {
        systemPrompt: body.system_prompt,
        promptProfileAutoIncludeLimit: body.prompt_profile_auto_include_limit,
      };
    },
    onSuccess: (settings) => {
      queryClient.setQueryData(OMNIHARNESS_SETTINGS_KEY, settings);
    },
  });
}

export function useAdminModelSettings(enabled = true) {
  return useQuery({
    queryKey: ADMIN_KEY,
    queryFn: fetchAdminModelSettings,
    staleTime: 30_000,
    enabled,
  });
}

export function useUpdateAdminModelSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (patch: {
      omniharnessModels?: string[];
      workloadClassificationEnabled?: boolean;
      workloadCustomCategories?: string[];
      policyModel?: string | null;
      smartRoutingDecisionModel?: string | null;
      smartRoutingPrompt?: string | null;
      smartRoutingCadence?: "per_turn" | "first_turn_only";
      turnSelectionUserMessageCount?: number;
    }) => {
      const response = await authenticatedFetch("/v1/admin/model-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(patch.omniharnessModels !== undefined
            ? { omniharness_models: patch.omniharnessModels }
            : {}),
          ...(patch.workloadClassificationEnabled !== undefined
            ? { workload_classification_enabled: patch.workloadClassificationEnabled }
            : {}),
          ...(patch.workloadCustomCategories !== undefined
            ? { workload_custom_categories: patch.workloadCustomCategories }
            : {}),
          ...(patch.policyModel !== undefined ? { policy_model: patch.policyModel } : {}),
          ...(patch.smartRoutingDecisionModel !== undefined
            ? { smart_routing_decision_model: patch.smartRoutingDecisionModel }
            : {}),
          ...(patch.smartRoutingPrompt !== undefined
            ? { smart_routing_prompt: patch.smartRoutingPrompt }
            : {}),
          ...(patch.smartRoutingCadence !== undefined
            ? { smart_routing_cadence: patch.smartRoutingCadence }
            : {}),
          ...(patch.turnSelectionUserMessageCount !== undefined
            ? { turn_selection_user_message_count: patch.turnSelectionUserMessageCount }
            : {}),
        }),
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    },
    onSuccess: async (_data, variables) => {
      if (variables.omniharnessModels !== undefined) {
        const admin = queryClient.getQueryData<AdminModelSettings>(ADMIN_KEY);
        optionsOverride = variables.omniharnessModels.map((id) => ({
          id,
          displayName: admin?.models.find((model) => model.id === id)?.displayName ?? id,
        }));
      }
      await queryClient.invalidateQueries({ queryKey: ADMIN_KEY });
    },
  });
}
