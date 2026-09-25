import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  archiveAgentTask,
  assignTaskItemWorker,
  createTaskItem,
  initializeWorker,
  cancelTaskItem,
  deleteTaskAsset,
  ensureBrokerSession,
  ensureSecretarySession,
  fetchAgentTasks,
  fetchAgentTaskBoardSearch,
  fetchBrokerProfile,
  fetchLiveAgentTasks,
  fetchSecretaryProfile,
  fetchTaskDashboard,
  fireTaskItem,
  closeTaskItem,
  interruptAgentQueueItem,
  moveTaskToQueueEnd,
  permanentlyDeleteAgentTask,
  reassignWorker,
  resetBrokerSession,
  resetSecretarySession,
  patchAgentTask,
  retryTaskItemDispatch,
  untrackWorker,
  updateTaskItem,
  type CreateTaskItemRequest,
  type DispatchPayload,
  type UpdateAgentTaskRequest,
  type UpdateTaskItemRequest,
  type WorkerAssignmentInput,
  type TaskDashboard,
  type AgentTaskBoardMatch,
} from "@/lib/agentTasksApi";
import { interrupt as interruptSession } from "@/lib/sessionsApi";
import { useChatStore } from "@/store/chatStore";
import { FIXTURE_TASK_LIST } from "@/shell/pmv2/fixtures/mockTaskDashboard";
import { isPmv2FixtureMode } from "@/shell/pmv2/fixtures/pmv2FixtureMode";
import {
  fixtureCloseItem,
  fixtureFireItem,
  fixtureRemoveAsset,
  fixtureRemoveItem,
  fixtureRetryItem,
  fixtureStopRunning,
  fixtureUpdateItem,
} from "@/shell/pmv2/fixtures/pmv2FixtureStore";
import { useFixtureDashboard } from "@/shell/pmv2/fixtures/useFixtureDashboard";

const fixtureEnabled = isPmv2FixtureMode();

function invalidateTaskQueries(queryClient: ReturnType<typeof useQueryClient>, taskId: string) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ["agent-task-dashboard", taskId] }),
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
        if (state === "live") {
          return FIXTURE_TASK_LIST.filter((task) => task.state !== "archived");
        }
        return FIXTURE_TASK_LIST;
      }
      return state === "live" ? fetchLiveAgentTasks() : fetchAgentTasks(state);
    },
    refetchInterval: fixtureEnabled ? false : 10_000,
  });
}

export function useTaskDashboard(
  taskId: string,
  options?: { enabled?: boolean },
): UseQueryResult<TaskDashboard> {
  const fixtureDashboard = useFixtureDashboard(taskId);
  const live = useQuery({
    queryKey: ["agent-task-dashboard", taskId],
    queryFn: () => fetchTaskDashboard(taskId),
    refetchInterval: 10_000,
    // Off-screen cards don't fetch or poll their dashboard; scrolling back
    // re-enables and TanStack refetches the stale data automatically.
    enabled: (options?.enabled ?? true) && !fixtureEnabled,
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

/** Debounce matches the command-palette search (300ms) so keystrokes don't
 * each hit the server. */
const BOARD_SEARCH_DEBOUNCE_MS = 300;

/** Server-side board search. Returns the matched tasks with per-entity match
 * ids; ``data`` stays undefined while the first request for a query is in
 * flight so the board can keep showing everything until results arrive. */
export function useAgentTaskBoardSearch(query: string): UseQueryResult<AgentTaskBoardMatch[]> {
  const trimmed = query.trim();
  const [debounced, setDebounced] = useState(trimmed);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(trimmed), BOARD_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [trimmed]);

  const live = useQuery({
    queryKey: ["agent-tasks-board-search", debounced],
    queryFn: () => fetchAgentTaskBoardSearch(debounced),
    enabled: !fixtureEnabled && debounced.length > 0,
    // Keep the previous query's results visible while the next one fetches,
    // so the board doesn't flash back to unfiltered on every keystroke.
    placeholderData: keepPreviousData,
  });

  if (fixtureEnabled) {
    // Fixture mode has no server; match against the fixture task list so the
    // fixture board stays demonstrable.
    const needle = debounced.toLowerCase();
    const matches: AgentTaskBoardMatch[] = debounced
      ? FIXTURE_TASK_LIST.filter(
          (task) =>
            task.title.toLowerCase().includes(needle) ||
            (task.description?.toLowerCase().includes(needle) ?? false) ||
            task.id.toLowerCase().includes(needle),
        ).map((task) => ({
          task_id: task.id,
          matched_in: ["task"],
          item_ids: [],
          asset_ids: [],
          worker_ids: [],
        }))
      : [];
    return {
      ...live,
      data: matches,
      isLoading: false,
      isPending: false,
      isError: false,
      error: null,
      isFetching: false,
      status: "success",
      fetchStatus: "idle",
    } as UseQueryResult<AgentTaskBoardMatch[]>;
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

export function useFireTaskItem(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      taskItemId,
      edited_payload,
    }: {
      taskItemId: string;
      edited_payload?: DispatchPayload;
    }) => {
      if (fixtureEnabled) {
        if (edited_payload) {
          fixtureUpdateItem(taskId, taskItemId, {
            title: edited_payload.title,
            instructions: edited_payload.instructions ?? null,
            description: edited_payload.description ?? null,
          });
        }
        fixtureFireItem(taskId, taskItemId);
        return;
      }
      await fireTaskItem(taskItemId, edited_payload);
    },
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function useCloseTaskItem(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (taskItemId: string) => {
      if (fixtureEnabled) {
        fixtureCloseItem(taskId, taskItemId);
        return;
      }
      await closeTaskItem(taskItemId);
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
      body: UpdateTaskItemRequest;
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
      void queueItemId;
      await cancelTaskItem(taskItemId);
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

export function useInitializeWorker(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workerId: string) => initializeWorker(workerId),
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function useDeleteTaskAsset(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (assetId: number) => {
      if (fixtureEnabled) {
        fixtureRemoveAsset(taskId, assetId);
        return;
      }
      await deleteTaskAsset(taskId, assetId);
    },
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function usePatchAgentTask(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateAgentTaskRequest) => patchAgentTask(taskId, body),
    onMutate: async (body) => {
      const key = ["agent-task-dashboard", taskId];
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<TaskDashboard>(key);
      if (previous) {
        queryClient.setQueryData<TaskDashboard>(key, {
          ...previous,
          task: { ...previous.task, ...body },
        });
      }
      return { previous };
    },
    onError: (_error, _body, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["agent-task-dashboard", taskId], context.previous);
      }
    },
    onSettled: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function useCreateTaskItem(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateTaskItemRequest) => createTaskItem(taskId, body),
    onSuccess: async () => invalidateTaskQueries(queryClient, taskId),
  });
}

export function useAssignTaskItemWorker(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (assignment: WorkerAssignmentInput) => assignTaskItemWorker(taskId, assignment),
    onSuccess: async () => invalidateTaskQueries(queryClient, taskId),
  });
}

export function useMoveTaskToQueueEnd(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => moveTaskToQueueEnd(taskId),
    onSuccess: async () => invalidateTaskQueries(queryClient, taskId),
  });
}

export function useUntrackWorker() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workerId: string) => untrackWorker(workerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["task-dashboard"] });
    },
  });
}

export function useReassignWorker() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ workerId, taskId }: { workerId: string; taskId: string }) =>
      reassignWorker(workerId, taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["task-dashboard"] });
    },
  });
}

export function useArchiveAgentTask(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => archiveAgentTask(taskId),
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}

export function usePermanentlyDeleteAgentTask(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => permanentlyDeleteAgentTask(taskId),
    onSuccess: async () => {
      await invalidateTaskQueries(queryClient, taskId);
    },
  });
}
