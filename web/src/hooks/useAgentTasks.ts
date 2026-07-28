import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ensureSecretarySession,
  fetchAgentTasks,
  fetchSecretaryProfile,
  fetchTaskDashboard,
  resetSecretarySession,
  resolveTaskItem,
  updateTaskItem,
  type DispatchPayload,
  type ItemResolution,
} from "@/lib/agentTasksApi";
import { useChatStore } from "@/store/chatStore";

export function useAgentTaskList(state = "active") {
  return useQuery({
    queryKey: ["agent-tasks", state],
    queryFn: () => fetchAgentTasks(state),
    refetchInterval: 10_000,
  });
}

export function useTaskDashboard(taskId: string) {
  return useQuery({
    queryKey: ["agent-task-dashboard", taskId],
    queryFn: () => fetchTaskDashboard(taskId),
    refetchInterval: 10_000,
  });
}

export function useSecretaryProfile() {
  return useQuery({
    queryKey: ["agent-task-secretary-profile"],
    queryFn: fetchSecretaryProfile,
    staleTime: 60_000,
    retry: false,
  });
}

export function useSecretarySession() {
  return useQuery({
    queryKey: ["agent-task-secretary-session"],
    queryFn: ensureSecretarySession,
    staleTime: 60_000,
    retry: false,
  });
}

export function useResetSecretarySession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: resetSecretarySession,
    onSuccess: async (session) => {
      await queryClient.invalidateQueries({ queryKey: ["agent-task-secretary-profile"] });
      await queryClient.invalidateQueries({ queryKey: ["agent-task-secretary-session"] });
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void useChatStore.getState().switchTo(session.conversation_id);
    },
  });
}

export function useResolveTaskItem(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      taskItemId,
      resolution,
      edited_payload,
    }: {
      taskItemId: string;
      resolution: ItemResolution;
      edited_payload?: DispatchPayload;
    }) => {
      await resolveTaskItem(taskItemId, { resolution, edited_payload });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-task-dashboard", taskId] });
      await queryClient.invalidateQueries({ queryKey: ["agent-tasks", "paused"] });
      await queryClient.invalidateQueries({ queryKey: ["agent-tasks", "active"] });
    },
  });
}

export function useUpdateTaskItem(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      taskItemId,
      body,
    }: {
      taskItemId: string;
      body: DispatchPayload & { title?: string; instructions?: string };
    }) => updateTaskItem(taskItemId, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-task-dashboard", taskId] });
    },
  });
}
