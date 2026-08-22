import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

export interface ToolGroup {
  id: string;
  title: string;
  order: number;
}

export interface ToolEntry {
  name: string;
  title: string;
  description: string;
  group: string;
  enabled: boolean;
  available: boolean;
}

export interface ToolPreferences {
  groups: ToolGroup[];
  tools: ToolEntry[];
  disabledTools: string[];
}

interface WireToolPreferences {
  object: "tool_preferences";
  groups: { id: string; title: string; order: number }[];
  tools: {
    name: string;
    title: string;
    description: string;
    group: string;
    enabled: boolean;
    available: boolean;
  }[];
  disabled_tools: string[];
}

async function fetchToolPreferences(): Promise<ToolPreferences> {
  const response = await authenticatedFetch("/v1/tool-preferences");
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const body = (await response.json()) as WireToolPreferences;
  return {
    groups: body.groups,
    tools: body.tools,
    disabledTools: body.disabled_tools,
  };
}

const KEY = ["tool-preferences"];

export function useToolPreferences() {
  return useQuery({
    queryKey: KEY,
    queryFn: fetchToolPreferences,
  });
}

export function useUpdateToolPreferences() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (disabledTools: string[]) => {
      const response = await authenticatedFetch("/v1/tool-preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disabled_tools: disabledTools }),
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return (await response.json()) as WireToolPreferences;
    },
    onSuccess: (data) => {
      queryClient.setQueryData<ToolPreferences>(KEY, {
        groups: data.groups,
        tools: data.tools,
        disabledTools: data.disabled_tools,
      });
    },
  });
}
