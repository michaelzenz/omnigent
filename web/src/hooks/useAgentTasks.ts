import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import {
  acceptAgentTaskPackage,
  activateWorkerLane,
  cancelAgentQueueItem,
  ensureBrokerSession,
  ensureSecretarySession,
  fetchAgentTasks,
  fetchBrokerProfile,
  fetchLiveAgentTasks,
  fetchSecretaryProfile,
  fetchTaskDashboard,
  interruptAgentQueueItem,
  rejectAgentTaskPackage,
  resetBrokerSession,
  resetSecretarySession,
  patchAgentTask,
  resolveTaskItem,
  retryTaskItemDispatch,
  updateTaskItem,
  updateWorkerLaneRole,
  type DispatchPayload,
  type ItemResolution,
  type TaskDashboard,
} from "@/lib/agentTasksApi";
import { interrupt as interruptSession } from "@/lib/sessionsApi";
import { useChatStore } from "@/store/chatStore";
import { FIXTURE_TASK_LIST } from "@/shell/puppyGarden/fixtures/mockTaskDashboard";
import { isPuppyGardenFixtureMode } from "@/shell/puppyGarden/fixtures/puppyGardenFixtureMode";
import {
  fixtureRemoveItem,
  fixtureResolveInboxItem,
  fixtureRetryItem,
  fixtureStopRunning,
  fixtureUpdateItem,
} from "@/shell/puppyGarden/fixtures/puppyGardenFixtureStore";
import { useFixtureDashboard } from "@/shell/puppyGarden/fixtures/useFixtureDashboard";

const fixtureEnabled = isPuppyGardenFixtureMode();

function invalidateTaskQueries(queryClient: ReturnType<typeof useQueryClient>, taskId: string) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ["agent-task-dashboard", taskId] }),
    queryClient.invalidateQueries({ queryKey: ["agent-tasks", "pending"] }),
    queryClient.invalidateQueries({ queryKey: ["agent-tasks", "live"] }),
    queryClient.invalidateQueries({ queryKey: ["agent-tasks", "active"] }),
    queryClient.invalidateQueries({ queryKey: ["agent-tasks", "idle"] }),
  ]);
}

export function useAgentTaskList(state = "active") {
  return useQuery({
    queryKey: ["agent-tasks", state, fixtureEnabled ? "fixture" : "live"],
    queryFn: () => {
      if (fixtureEnabled) {
        if (state === "pending") {
          return FIXTURE_TASK_LIST.filter((task) => task.state === "pending");
        }
        if (state === "live") {
          return FIXTURE_TASK_LIST.filter((task) => task.state !== "pending");
        }
        return FIXTURE_TASK_LIST;
      }
      return state === "live" ? fetchLiveAgentTasks() : fetchAgentTasks(state);
    },
    refetchInterval: fixtureEnabled ? false : 10_000,
  });
}

export function useTaskDashboard(taskId: string): UseQueryResult<TaskDashboard> {
  const fixtureDashboard = useFixtureDashboard(taskId);
  const live = useQuery({
    queryKey: ["agent-task-dashboard", taskId],
    queryFn: () => fetchTaskDashboard(taskId),
    refetchInterval: 10_000,
    enabled: !fixtureEnabled,
  });

  if (fixtureEnabled) {
    return {
      ...live,
      data: fixtureDashboard ?? undefined,
      isLoading: false,
      isPending: false,
      isError: false,
      error: null,
      isFetching: false,
      status: fixtureDashboard ? "success" : "pending",
      fetchStatus: "idle",
    } as UseQueryResult<TaskDashboard>;
  }

  return live;
}

export function useSecretaryProfile() {
  return useQuery({
    queryKey: ["agent-task-secretary-profile"],
    queryFn: fetchSecretaryProfile,
    staleTime: 60_000,
    retry: false,
    enabled: !fixtureEnabled,
  });
}

export function useSecretarySession() {
  return useQuery({
    queryKey: ["agent-task-secretary-session"],
    queryFn: ensureSecretarySession,
    staleTime: 60_000,
    retry: false,
    enabled: !fixtureEnabled,
  });
}

export function useBrokerProfile() {
  return useQuery({
    queryKey: ["agent-task-broker-profile"],
    queryFn: fetchBrokerProfile,
    staleTime: 60_000,
    retry: false,
    enabled: !fixtureEnabled,
  });
}

export function useBrokerSession() {
  return useQuery({
    queryKey: ["agent-task-broker-session"],
    queryFn: ensureBrokerSession,
    staleTime: 60_000,
    retry: false,
    enabled: !fixtureEnabled,
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

export function useResetBrokerSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: resetBrokerSession,
    onSuccess: async (session) => {
      await queryClient.invalidateQueries({ queryKey: ["agent-task-broker-profile"] });
      await queryClient.invalidateQueries({ queryKey: ["agent-task-broker-session"] });
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
      edited_payload?: DispatchPayload & { description?: string };
    }) => {
      if (fixtureEnabled) {
        if (resolution === "reject_item") {
          fixtureResolveInboxItem(taskId, taskItemId, "reject_item");
          return;
        }
        if (edited_payload) {
          fixtureUpdateItem(taskId, taskItemId, {
            title: edited_payload.title,
            instructions: edited_payload.instructions ?? null,
            description: edited_payload.description ?? null,
          });
        }
        fixtureResolveInboxItem(taskId, taskItemId, "accept_item");
        return;
      }
      await resolveTaskItem(taskItemId, { resolution, edited_payload });
    },
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
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
      body: DispatchPayload & { title?: string; instructions?: string; description?: string };
    }) => {
      if (fixtureEnabled) {
        fixtureUpdateItem(taskId, taskItemId, {
          title: body.title,
          instructions: body.instructions ?? null,
          description: body.description ?? null,
        });
        return;
      }
      return updateTaskItem(taskItemId, body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-task-dashboard", taskId] });
    },
  });
}

export function useStopTaskItem(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      taskItemId,
      queueItemId,
      conversationId,
    }: {
      taskItemId: string;
      queueItemId?: string | null;
      conversationId?: string | null;
    }) => {
      if (fixtureEnabled) {
        fixtureStopRunning(taskId, taskItemId);
        return;
      }
      if (queueItemId) {
        await interruptAgentQueueItem(queueItemId);
        return;
      }
      if (conversationId) {
        await interruptSession(conversationId);
        return;
      }
      throw new Error("No queue item or session to interrupt");
    },
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function useRemoveTaskItem(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      taskItemId,
      queueItemId,
    }: {
      taskItemId: string;
      queueItemId?: string | null;
    }) => {
      if (fixtureEnabled) {
        fixtureRemoveItem(taskId, taskItemId);
        return;
      }
      if (!queueItemId) {
        throw new Error("No queue item to remove");
      }
      await cancelAgentQueueItem(queueItemId);
    },
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function useRetryTaskItem(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (taskItemId: string) => {
      if (fixtureEnabled) {
        fixtureRetryItem(taskId, taskItemId);
        return;
      }
      await retryTaskItemDispatch(taskItemId);
    },
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function useUpdateAgentTaskManagerRole(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (managerRoleKey: string) =>
      patchAgentTask(taskId, { manager_role_key: managerRoleKey }),
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function useAcceptAgentTaskPackage(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => acceptAgentTaskPackage(taskId),
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
      await queryClient.invalidateQueries({ queryKey: ["agent-tasks", "pending"] });
      await queryClient.invalidateQueries({ queryKey: ["agent-tasks", "live"] });
    },
  });
}

export function useRejectAgentTaskPackage(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => rejectAgentTaskPackage(taskId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-tasks", "pending"] });
      await queryClient.invalidateQueries({ queryKey: ["agent-tasks", "live"] });
    },
  });
}

export function useUpdateWorkerLaneRole(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ workerId, roleKey }: { workerId: string; roleKey: string }) =>
      updateWorkerLaneRole(workerId, roleKey),
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function useActivateWorkerLane(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workerId: string) => activateWorkerLane(workerId),
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}
