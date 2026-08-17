export const SDK_HARNESS = "openai-agents";

export interface SdkModelOption {
  id: string;
  displayName: string;
}

export const SDK_MODEL_OPTIONS: readonly SdkModelOption[] = [
  { id: "databricks-glm-5-2", displayName: "GLM 5.2" },
  { id: "databricks-kimi-k3", displayName: "Kimi K3" },
];
