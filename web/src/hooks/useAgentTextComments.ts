import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

export interface AgentTextComment {
  id: string;
  conversation_id: string;
  conversation_item_id: string;
  start_offset: number;
  end_offset: number;
  selected_text: string;
  prefix_context: string;
  suffix_context: string;
  body: string;
  created_at: number;
  updated_at: number;
}

export interface AgentTextCommentAnchor {
  conversation_item_id: string;
  start_offset: number;
  end_offset: number;
  selected_text: string;
  prefix_context: string;
  suffix_context: string;
}

const queryKey = (sessionId: string) => ["agent-text-comments", sessionId] as const;

async function jsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return (await response.json()) as T;
}

export function useAgentTextComments(sessionId: string | undefined) {
  return useQuery({
    queryKey: queryKey(sessionId ?? ""),
    enabled: !!sessionId,
    queryFn: async () =>
      jsonOrThrow<AgentTextComment[]>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId!)}/agent-text-comments`,
        ),
      ),
  });
}

export function useAddAgentTextComment(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AgentTextCommentAnchor & { body: string }) =>
      jsonOrThrow<AgentTextComment>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-comments`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          },
        ),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKey(sessionId) }),
  });
}

export function useUpdateAgentTextComment(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: string }) =>
      jsonOrThrow<AgentTextComment>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-comments/${encodeURIComponent(id)}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ body }),
          },
        ),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKey(sessionId) }),
  });
}

export function useDeleteAgentTextComment(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      jsonOrThrow<{ deleted: boolean }>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-comments/${encodeURIComponent(id)}`,
          { method: "DELETE" },
        ),
      ),
    onSuccess: (_, id) => {
      client.setQueryData<AgentTextComment[]>(queryKey(sessionId), (current) =>
        current?.filter((comment) => comment.id !== id),
      );
      return client.invalidateQueries({ queryKey: queryKey(sessionId) });
    },
  });
}

export function useDeleteAgentTextCommentsBatch(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (commentIds: string[]) =>
      jsonOrThrow<{ deleted_comment_ids: string[] }>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-comments/delete-batch`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ comment_ids: commentIds }),
          },
        ),
      ),
    onSuccess: (_, commentIds) => {
      const deleted = new Set(commentIds);
      client.setQueryData<AgentTextComment[]>(queryKey(sessionId), (current) =>
        current?.filter((comment) => !deleted.has(comment.id)),
      );
      return client.invalidateQueries({ queryKey: queryKey(sessionId) });
    },
  });
}
