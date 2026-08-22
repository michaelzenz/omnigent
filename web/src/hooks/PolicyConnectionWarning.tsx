import { useEffect, useMemo, useRef } from "react";
import { showToast } from "@/components/ui/toast";
import { useDefaultPolicies } from "@/hooks/useDefaultPolicies";
import { useIsAdmin } from "@/hooks/useIsAdmin";
import { useAdminModelSettings } from "@/hooks/useModelSettings";
import { usePolicyRegistry } from "@/hooks/usePolicies";
import { isSingleUserMode } from "@/lib/capabilities";
import { useServerInfo } from "@/lib/CapabilitiesContext";

/** Warn globally when an enabled AI-backed policy cannot reach Databricks. */
export function PolicyConnectionWarning() {
  const policies = useDefaultPolicies();
  const registry = usePolicyRegistry();
  const serverInfo = useServerInfo();
  const canReadAdminSettings =
    useIsAdmin() || (serverInfo !== "loading" && isSingleUserMode(serverInfo));
  const modelSettings = useAdminModelSettings(canReadAdminSettings);
  const warningShownRef = useRef(false);

  const hasEnabledLlmPolicy = useMemo(() => {
    const llmHandlers = new Set(
      (registry.data ?? []).filter((entry) => entry.requires_llm).map((entry) => entry.handler),
    );
    return (policies.data ?? []).some(
      (policy) => policy.enabled && llmHandlers.has(policy.handler),
    );
  }, [policies.data, registry.data]);

  const shouldWarn =
    canReadAdminSettings &&
    policies.isSuccess &&
    registry.isSuccess &&
    modelSettings.isSuccess &&
    hasEnabledLlmPolicy &&
    modelSettings.data.databricksConnected === false;

  useEffect(() => {
    if (!shouldWarn) {
      warningShownRef.current = false;
      return;
    }
    if (warningShownRef.current) return;
    warningShownRef.current = true;
    showToast(
      <span>
        <strong className="text-destructive">Intent-based policy needs Databricks.</strong> An
        AI-backed policy is enabled, but no Databricks connection is available.
      </span>,
      { duration: 0 },
    );
  }, [shouldWarn]);

  return null;
}
