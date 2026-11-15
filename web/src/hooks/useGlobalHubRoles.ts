import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useStopAndDeleteConversation } from "@/hooks/useConversations";
import {
  type BoardSession,
  GLOBALHUB_MANAGERS_QUERY_KEY,
  GLOBALHUB_TASKS_QUERY_KEY,
} from "@/hooks/useGlobalHubTasks";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse } from "@/lib/sessionsApi";

/** A role that runs the user's board, with its session and the work waiting for it. */
export interface BoardRole {
  /** ``"broker"``, ``"custodian"`` or a manager role such as ``"manager:default"``. */
  role: string;
  title: string;
  /** Set on a manager's row. */
  manager_id: string | null;
  /** ``null`` while the role has no session; a broker or custodian gets one when it has work. */
  session_id: string | null;
  session: BoardSession | null;
  /** Events waiting for the broker or a manager, or clicks queued for the custodian. */
  waiting: number;
}

const ROLES_QUERY_KEY = ["globalhub-roles"] as const;

async function sendRoleRequest(path: string, method: "POST" | "DELETE"): Promise<Response> {
  const res = await authenticatedFetch(path, { method });
  if (!res.ok) throw await apiErrorFromResponse(res);
  return res;
}

async function fetchBoardRoles(): Promise<BoardRole[]> {
  const res = await authenticatedFetch("/v1/agent-tasks/board/roles");
  if (!res.ok) throw await apiErrorFromResponse(res);
  return ((await res.json()) as { data: BoardRole[] }).data;
}

/** The broker, the custodian and each manager, refreshed while a view shows them. */
export function useBoardRoles() {
  return useQuery({
    queryKey: ROLES_QUERY_KEY,
    queryFn: fetchBoardRoles,
    refetchInterval: 10_000,
  });
}

/** Delete a broker's or custodian's session through main, then start a fresh one. */
export function useResetRoleSession() {
  const queryClient = useQueryClient();
  const deleteSession = useStopAndDeleteConversation();
  return useMutation<void, Error, BoardRole>({
    mutationFn: async (row) => {
      if (row.session_id) await deleteSession.mutateAsync({ id: row.session_id });
      await sendRoleRequest(
        `/v1/agent-tasks/roles/${encodeURIComponent(row.role)}/session`,
        "POST",
      );
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ROLES_QUERY_KEY }),
  });
}

/** Delete a manager, then its session through main, so the session gets main's full teardown. */
export function useDeleteManager() {
  const queryClient = useQueryClient();
  const deleteSession = useStopAndDeleteConversation();
  return useMutation<void, Error, string>({
    mutationFn: async (managerId) => {
      const res = await sendRoleRequest(
        `/v1/agent-tasks/managers/${encodeURIComponent(managerId)}`,
        "DELETE",
      );
      const { conversation_id } = (await res.json()) as { conversation_id: string | null };
      if (conversation_id) await deleteSession.mutateAsync({ id: conversation_id });
    },
    onSettled: async () => {
      await Promise.all(
        [ROLES_QUERY_KEY, GLOBALHUB_MANAGERS_QUERY_KEY, GLOBALHUB_TASKS_QUERY_KEY].map((queryKey) =>
          queryClient.invalidateQueries({ queryKey }),
        ),
      );
    },
  });
}

/** Dismiss the work waiting for a role: its events, or the custodian's queued clicks. */
export function useDismissRoleWaiting() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, BoardRole>({
    mutationFn: async (row) => {
      const path = row.manager_id
        ? `/v1/agent-tasks/managers/${encodeURIComponent(row.manager_id)}/dismiss-waiting`
        : `/v1/agent-tasks/roles/${encodeURIComponent(row.role)}/dismiss-waiting`;
      await sendRoleRequest(path, "POST");
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ROLES_QUERY_KEY }),
  });
}
