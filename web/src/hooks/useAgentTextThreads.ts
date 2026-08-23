import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ConversationItem } from "@/lib/conversationItems";
import { authenticatedFetch } from "@/lib/identity";
import { postEvent } from "@/lib/sessionsApi";
import type { AgentTextCommentAnchor } from "./useAgentTextComments";

export type AgentTextThreadState = "queued" | "running" | "answered" | "failed" | "resolved";
export type AgentTextThreadView = "open" | "resolved";

export interface AgentTextThread {
  id: string;
  conversation_id: string;
  source_item_id: string;
  start_offset: number;
  end_offset: number;
  selected_text: string;
  prefix_context: string;
  suffix_context: string;
  user_comment: string;
  state: AgentTextThreadState;
  user_item_id: string | null;
  response_id: string | null;
  failure_message: string | null;
  resolved_at: number | null;
  created_at: number;
  updated_at: number;
  source_position: number | null;
  items: ConversationItem[];
}

export type AddAgentTextThreadPayload = AgentTextCommentAnchor & {
  client_request_id: string;
  comment: string;
};

export interface AgentTextThreadCapability {
  supported: boolean;
  reason: "unsupported_harness" | "openai_sdk_unavailable" | null;
}

const key = (sessionId: string, view: AgentTextThreadView) =>
  ["agent-text-threads", sessionId, view] as const;

const capabilityKey = (sessionId: string) =>
  ["agent-text-threads", sessionId, "capability"] as const;

async function jsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return (await response.json()) as T;
}

function invalidate(client: ReturnType<typeof useQueryClient>, sessionId: string) {
  return client.invalidateQueries({ queryKey: ["agent-text-threads", sessionId] });
}

function threadPrompt(thread: AgentTextThread): string {
  const quote = thread.selected_text
    .split("\n")
    .map((line) => `> ${line}`)
    .join("\n");
  return `Regarding this excerpt from your earlier response:\n\n${quote}\n\nUser comment:\n${thread.user_comment}`;
}

async function markFailed(sessionId: string, threadId: string, cause: unknown): Promise<void> {
  const message = cause instanceof Error ? cause.message : "Could not send threaded comment.";
  await jsonOrThrow<AgentTextThread>(
    await authenticatedFetch(
      `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-threads/${encodeURIComponent(threadId)}/fail`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      },
    ),
  );
}

async function submitThread(sessionId: string, thread: AgentTextThread): Promise<AgentTextThread> {
  try {
    const result = await postEvent(sessionId, {
      type: "message",
      data: {
        role: "user",
        content: [{ type: "input_text", text: threadPrompt(thread) }],
      },
      comment_thread_id: thread.id,
    });
    if (result.denied) throw new Error("The comment was denied by policy.");
    return thread;
  } catch (cause) {
    await markFailed(sessionId, thread.id, cause);
    return thread;
  }
}

export function useAgentTextThreadCapability(sessionId: string | undefined) {
  return useQuery({
    queryKey: capabilityKey(sessionId ?? ""),
    enabled: !!sessionId,
    queryFn: async () =>
      jsonOrThrow<AgentTextThreadCapability>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId!)}/agent-text-threads/capability`,
        ),
      ),
  });
}

export function useAgentTextThreads(
  sessionId: string | undefined,
  view: AgentTextThreadView = "open",
) {
  return useQuery({
    queryKey: key(sessionId ?? "", view),
    enabled: !!sessionId,
    queryFn: async () =>
      jsonOrThrow<AgentTextThread[]>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId!)}/agent-text-threads?state=${view}`,
        ),
      ),
    refetchInterval: (query) => {
      const threads = query.state.data;
      return threads?.some((thread) => thread.state === "queued" || thread.state === "running")
        ? 750
        : false;
    },
  });
}

export function useCreateAgentTextThread(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AddAgentTextThreadPayload) => {
      const thread = await jsonOrThrow<AgentTextThread>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-threads`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ...payload,
              source_item_id: payload.conversation_item_id,
            }),
          },
        ),
      );
      return submitThread(sessionId, thread);
    },
    onSuccess: () => invalidate(client, sessionId),
    onError: () => invalidate(client, sessionId),
  });
}

function useActionMutation(action: "retry" | "resolve" | "fail", sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, message }: { id: string; message?: string }) =>
      jsonOrThrow<AgentTextThread>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-threads/${encodeURIComponent(id)}/${action}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: action === "fail" ? JSON.stringify({ message }) : undefined,
          },
        ),
      ),
    onSuccess: () => invalidate(client, sessionId),
  });
}

export function useRetryAgentTextThread(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id }: { id: string }) => {
      const thread = await jsonOrThrow<AgentTextThread>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-threads/${encodeURIComponent(id)}/retry`,
          { method: "POST" },
        ),
      );
      return submitThread(sessionId, thread);
    },
    onSuccess: () => invalidate(client, sessionId),
    onError: () => invalidate(client, sessionId),
  });
}

export function useResolveAgentTextThread(sessionId: string) {
  return useActionMutation("resolve", sessionId);
}

export function useFailAgentTextThread(sessionId: string) {
  return useActionMutation("fail", sessionId);
}

export function useDeleteAgentTextThread(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      jsonOrThrow<{ deleted: boolean }>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-threads/${encodeURIComponent(id)}`,
          { method: "DELETE" },
        ),
      ),
    onSuccess: () => invalidate(client, sessionId),
  });
}
