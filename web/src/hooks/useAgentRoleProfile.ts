import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchAgentRoleProfile,
  importRoleAgent,
  updateAgentRoleProfile,
  updateRolePrompt,
  type SecretaryProfile,
  type UpdateAgentRoleProfileRequest,
} from "@/lib/agentTasksApi";

export function agentRoleProfileQueryKey(role: string) {
  return ["agent-role-profile", role] as const;
}

export function useAgentRoleProfile(role: string | undefined, enabled = true) {
  return useQuery({
    queryKey: agentRoleProfileQueryKey(role ?? ""),
    queryFn: () => fetchAgentRoleProfile(role!),
    enabled: enabled && role != null,
  });
}

export function useUpdateAgentRoleProfile(role: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateAgentRoleProfileRequest) => updateAgentRoleProfile(role, body),
    onSuccess: (data: SecretaryProfile) => {
      queryClient.setQueryData(agentRoleProfileQueryKey(role), data);
    },
  });
}

export function useImportRoleAgent(role: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string) => importRoleAgent(role, agentId),
    onSuccess: (data: SecretaryProfile) => {
      queryClient.setQueryData(agentRoleProfileQueryKey(role), data);
    },
  });
}

export function useUpdateRolePrompt(role: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (prompt: string) => updateRolePrompt(role, prompt),
    onSuccess: (data: SecretaryProfile) => {
      queryClient.setQueryData(agentRoleProfileQueryKey(role), data);
    },
  });
}
