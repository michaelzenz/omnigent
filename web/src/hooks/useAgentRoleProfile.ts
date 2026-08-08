import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchAgentRoleProfile,
  updateAgentRoleProfile,
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
