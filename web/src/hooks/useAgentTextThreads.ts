import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import type { ConversationItem } from "@/lib/conversationItems";
import { authenticatedFetch } from "@/lib/identity";
import { ApiError, postEvent } from "@/lib/sessionsApi";
import type { AgentTextCommentAnchor } from "./useAgentTextComments";

export type AgentTextThreadState =
  "initializing" | "queued" | "submitting" | "running" | "answered" | "failed" | "resolved";
export type AgentTextThreadView = "open" | "resolved";

export interface AgentTextThreadTurn {
  id: string;
  thread_id: string;
  sequence: number;
  client_request_id: string;
  submission_id: string;
  question: string;
  selected_quote: string | null;
  state: AgentTextThreadState;
  user_item_id: string | null;
  response_id: string | null;
  failure_message: string | null;
  created_at: number;
  updated_at: number;
  items: ConversationItem[];
}

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
  turns: AgentTextThreadTurn[];
}

export type AddAgentTextThreadPayload = AgentTextCommentAnchor & {
  client_request_id: string;
  comment: string;
};

export interface AddAgentTextThreadTurnPayload {
  client_request_id: string;
  question: string;
  selected_quote?: string | null;
}

export interface AgentTextThreadCapability {
  supported: boolean;
  reason: "unsupported_harness" | "openai_sdk_unavailable" | null;
}

const key = (sessionId: string, view: AgentTextThreadView) =>
  ["agent-text-threads", sessionId, view] as const;

const capabilityKey = (sessionId: string) =>
  ["agent-text-threads", sessionId, "capability"] as const;

const turnSubmissionsInFlight = new Set<string>();

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

async function submitThread(sessionId: string, thread: AgentTextThread): Promise<AgentTextThread> {
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
  const client = useQueryClient();
  const queryKey = key(sessionId ?? "", view);
  const query = useQuery({
    queryKey,
    enabled: !!sessionId,
    queryFn: async () => {
      const fetched = await jsonOrThrow<AgentTextThread[]>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId!)}/agent-text-threads?state=${view}`,
        ),
      );
      const current = client.getQueryData<AgentTextThread[]>(queryKey) ?? [];
      return fetched.map((thread) => {
        const currentThread = current.find((item) => item.id === thread.id);
        const durableRequests = new Set(thread.turns.map((turn) => turn.client_request_id));
        const optimistic =
          currentThread?.turns.filter(
            (turn) => turn.state === "initializing" && !durableRequests.has(turn.client_request_id),
          ) ?? [];
        return optimistic.length > 0
          ? { ...thread, turns: [...thread.turns, ...optimistic] }
          : thread;
      });
    },
    refetchInterval: (queryState) => {
      const threads = queryState.state.data;
      return threads?.some(
        (thread) =>
          thread.state === "initializing" ||
          thread.state === "queued" ||
          thread.state === "running" ||
          thread.turns.some(
            (turn) =>
              turn.state === "initializing" ||
              turn.state === "queued" ||
              turn.state === "submitting" ||
              turn.state === "running",
          ),
      )
        ? 750
        : false;
    },
  });

  useEffect(() => {
    if (!sessionId || view !== "open" || !query.data) return;
    for (const thread of query.data) {
      const orderedTurns = [...thread.turns].sort((left, right) => left.sequence - right.sequence);
      const turn = orderedTurns.find((candidate) => candidate.state === "queued");
      const blockedByFailedEarlierTurn =
        turn !== undefined &&
        orderedTurns.some(
          (candidate) =>
            candidate.sequence < turn.sequence &&
            candidate.state === "failed" &&
            candidate.user_item_id === null,
        );
      if (!turn || blockedByFailedEarlierTurn || turnSubmissionsInFlight.has(turn.id)) continue;
      turnSubmissionsInFlight.add(turn.id);
      void submitTurn(sessionId, turn)
        .catch(() => undefined)
        .finally(() => {
          turnSubmissionsInFlight.delete(turn.id);
          void invalidate(client, sessionId);
        });
    }
  }, [client, query.data, sessionId, view]);

  return query;
}

export function useCreateAgentTextThread(sessionId: string) {
  const client = useQueryClient();
  const openKey = key(sessionId, "open");
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
    onMutate: async (payload) => {
      const previous = client.getQueryData<AgentTextThread[]>(openKey);
      const cancellation = client.cancelQueries({ queryKey: openKey });
      const now = Date.now() * 1_000;
      const optimisticId = `initializing:${payload.client_request_id}`;
      const optimistic: AgentTextThread = {
        id: optimisticId,
        conversation_id: sessionId,
        source_item_id: payload.conversation_item_id,
        start_offset: payload.start_offset,
        end_offset: payload.end_offset,
        selected_text: payload.selected_text,
        prefix_context: payload.prefix_context,
        suffix_context: payload.suffix_context,
        user_comment: payload.comment,
        state: "initializing",
        user_item_id: null,
        response_id: null,
        failure_message: null,
        resolved_at: null,
        created_at: now,
        updated_at: now,
        source_position: null,
        items: [],
        turns: [],
      };
      client.setQueryData<AgentTextThread[]>(openKey, [...(previous ?? []), optimistic]);
      await cancellation;
      return { optimisticId, previous };
    },
    onSuccess: (thread, _payload, context) => {
      client.setQueryData<AgentTextThread[]>(openKey, (current = []) => {
        const withoutOptimistic = current.filter((item) => item.id !== context.optimisticId);
        return withoutOptimistic.some((item) => item.id === thread.id)
          ? withoutOptimistic
          : [...withoutOptimistic, thread];
      });
      return invalidate(client, sessionId);
    },
    onError: (_error, _payload, context) => {
      client.setQueryData(openKey, context?.previous ?? []);
      return invalidate(client, sessionId);
    },
  });
}

function turnPrompt(turn: AgentTextThreadTurn): string {
  const quote = turn.selected_quote
    ? `Regarding this excerpt from your response:\n\n${turn.selected_quote
        .split("\n")
        .map((line) => `> ${line}`)
        .join("\n")}\n\n`
    : "";
  return `${quote}User follow-up:\n${turn.question}`;
}

async function submitTurn(
  sessionId: string,
  turn: AgentTextThreadTurn,
): Promise<AgentTextThreadTurn> {
  try {
    const result = await postEvent(sessionId, {
      type: "message",
      data: {
        role: "user",
        content: [{ type: "input_text", text: turnPrompt(turn) }],
      },
      // The existing wire field is the runner correlation id. For follow-ups it
      // carries the turn id so each response binds to its exact question.
      comment_thread_id: turn.submission_id,
    });
    if (result.denied) throw new Error("The follow-up was denied by policy.");
    return turn;
  } catch (cause) {
    // Another tab may have claimed this queued turn first. Its request owns the
    // dispatch; a query refresh will observe the resulting item/response IDs.
    if (cause instanceof ApiError && cause.status === 409) return turn;
    throw cause;
  }
}

export function useCreateAgentTextThreadTurn(sessionId: string, threadId: string) {
  const client = useQueryClient();
  const openKey = key(sessionId, "open");
  return useMutation({
    mutationFn: async (payload: AddAgentTextThreadTurnPayload) =>
      jsonOrThrow<AgentTextThreadTurn>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-threads/${encodeURIComponent(threadId)}/turns`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          },
        ),
      ),
    onMutate: async (payload) => {
      await client.cancelQueries({ queryKey: openKey });
      const previous = client.getQueryData<AgentTextThread[]>(openKey);
      const optimisticId = `initializing:${payload.client_request_id}`;
      client.setQueryData<AgentTextThread[]>(openKey, (current = []) =>
        current.map((thread) => {
          if (thread.id !== threadId) return thread;
          const sequence = Math.max(0, ...thread.turns.map((turn) => turn.sequence)) + 1;
          return {
            ...thread,
            turns: [
              ...thread.turns,
              {
                id: optimisticId,
                thread_id: threadId,
                sequence,
                client_request_id: payload.client_request_id,
                submission_id: optimisticId,
                question: payload.question,
                selected_quote: payload.selected_quote ?? null,
                state: "initializing",
                user_item_id: null,
                response_id: null,
                failure_message: null,
                created_at: Date.now() * 1_000,
                updated_at: Date.now() * 1_000,
                items: [],
              },
            ],
          };
        }),
      );
      return { optimisticId, previous };
    },
    onSuccess: (turn, _payload, context) => {
      client.setQueryData<AgentTextThread[]>(openKey, (current = []) =>
        current.map((thread) =>
          thread.id === threadId
            ? (() => {
                const currentServerTurn = thread.turns.find((item) => item.id === turn.id);
                const replacement = currentServerTurn ? { ...turn, ...currentServerTurn } : turn;
                return {
                  ...thread,
                  turns: [
                    ...thread.turns.filter(
                      (item) => item.id !== context.optimisticId && item.id !== turn.id,
                    ),
                    replacement,
                  ].sort((left, right) => left.sequence - right.sequence),
                };
              })()
            : thread,
        ),
      );
    },
    onError: (_error, _payload, context) => {
      client.setQueryData<AgentTextThread[]>(openKey, (current = []) =>
        current.map((thread) => ({
          ...thread,
          turns: thread.turns.filter((turn) => turn.id !== context?.optimisticId),
        })),
      );
      return invalidate(client, sessionId);
    },
  });
}

export function useRetryAgentTextThreadTurn(sessionId: string, threadId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (turnId: string) =>
      jsonOrThrow<AgentTextThreadTurn>(
        await authenticatedFetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}/agent-text-threads/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}/retry`,
          { method: "POST" },
        ),
      ),
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
