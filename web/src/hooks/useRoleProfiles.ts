import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createManagerRoleProfile,
  createWorkerRoleProfile,
  deleteAgentRoleProfile,
  fetchRoleProfiles,
  MANAGER_ROLE_PREFIX,
  WORKER_ROLE_PREFIX,
  type CreateManagerRoleProfileRequest,
  type RoleProfileSummary,
} from "@/lib/agentTasksApi";
import { agentRoleProfileQueryKey } from "@/hooks/useAgentRoleProfile";

export function roleProfilesQueryKey(prefix?: string) {
  return ["agent-role-profiles", prefix ?? "all"] as const;
}

export function useRoleProfiles(prefix?: string) {
  return useQuery({
    queryKey: roleProfilesQueryKey(prefix),
    queryFn: () => fetchRoleProfiles(prefix),
  });
}

function useCreateRoleProfile(
  createFn: (body: CreateManagerRoleProfileRequest) => Promise<RoleProfileSummary>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createFn,
    onSuccess: (profile: RoleProfileSummary) => {
      void queryClient.invalidateQueries({ queryKey: ["agent-role-profiles"] });
      queryClient.setQueryData(agentRoleProfileQueryKey(profile.role), profile);
    },
  });
}

export function useCreateManagerRoleProfile() {
  return useCreateRoleProfile(createManagerRoleProfile);
}

export function useCreateWorkerRoleProfile() {
  return useCreateRoleProfile(createWorkerRoleProfile);
}

export function useCreateTemplateRoleProfile(rolePrefix: string) {
  if (rolePrefix === MANAGER_ROLE_PREFIX) {
    return useCreateManagerRoleProfile();
  }
  if (rolePrefix === WORKER_ROLE_PREFIX) {
    return useCreateWorkerRoleProfile();
  }
  throw new Error(`Unsupported template role prefix: ${rolePrefix}`);
}

export function useDeleteRoleProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (role: string) => deleteAgentRoleProfile(role),
    onSuccess: (_data, role) => {
      void queryClient.invalidateQueries({ queryKey: ["agent-role-profiles"] });
      queryClient.removeQueries({ queryKey: agentRoleProfileQueryKey(role) });
    },
  });
}
