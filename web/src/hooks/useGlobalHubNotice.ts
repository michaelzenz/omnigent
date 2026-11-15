import { useSyncExternalStore } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse } from "@/lib/sessionsApi";

/** A control the user sets before clicking an action: a toggle or a text box. */
export interface SystemNoticeInput {
  id: string;
  kind: "toggle" | "text" | "textarea";
  label: string | null;
  /** Inputs with the same group, e.g. the sessions on one host, switch on or off together. */
  group: string | null;
  placeholder: string | null;
  /** A toggle's initial value. */
  default: boolean | null;
  required: boolean | null;
}

/** A button on a notice; a click carries the values of its inputs. */
export interface SystemNoticeAction {
  id: string;
  label: string | null;
  inputs: SystemNoticeInput[];
}

/** The notice pinned on the user's board, e.g. the custodian's cleanup proposal. */
export interface SystemNotice {
  id: string;
  title: string;
  body_kind: "markdown" | "html";
  body: string | null;
  actions: SystemNoticeAction[];
  state: "active" | "resolved";
  publisher_session_id: string | null;
  /** The publishing session's title. */
  publisher_label: string | null;
  created_at: number;
  updated_at: number | null;
  resolved_at: number | null;
}

/** A click's values by input id: ``true`` or ``false`` for a toggle, text for a text box. */
export type SystemNoticeValues = Record<string, boolean | string>;

const NOTICE_PATH = "/v1/agent-tasks/system-notice";
const SYSTEM_NOTICE_QUERY_KEY = ["globalhub-system-notice"] as const;
const SEEN_NOTICE_STORAGE_KEY = "omnigent.globalhub.seen-notice-id";

async function sendNoticeRequest(path: string, body?: unknown): Promise<Response> {
  const res = await authenticatedFetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await apiErrorFromResponse(res);
  return res;
}

async function fetchSystemNotice(): Promise<SystemNotice | null> {
  const res = await sendNoticeRequest(NOTICE_PATH);
  return ((await res.json()) as { notice: SystemNotice | null }).notice;
}

/** The user's active system notice, or ``null``; the board card and the sidebar dot share it. */
export function useSystemNotice() {
  return useQuery({
    queryKey: SYSTEM_NOTICE_QUERY_KEY,
    queryFn: fetchSystemNotice,
    // The custodian publishes and clears notices in the background.
    refetchInterval: 10_000,
    staleTime: 5_000,
    // A server without GlobalHub answers 404; the next poll asks again.
    retry: false,
  });
}

/** Send a click on an action of the active notice to the custodian. */
export function useClickSystemNoticeAction() {
  return useMutation<void, Error, { actionId: string; inputs: SystemNoticeValues }>({
    mutationFn: async ({ actionId, inputs }) => {
      await sendNoticeRequest(`${NOTICE_PATH}/actions/${encodeURIComponent(actionId)}/click`, {
        inputs,
      });
    },
  });
}

/** Take the active notice off the board; no session hears of it. */
export function useDismissSystemNotice() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      await sendNoticeRequest(`${NOTICE_PATH}/dismiss`, {});
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: SYSTEM_NOTICE_QUERY_KEY }),
  });
}

// The id of the last notice the user saw on the board, kept in this browser.
let seenNoticeId = readSeenNoticeId();
const seenListeners = new Set<() => void>();

function readSeenNoticeId(): string | null {
  try {
    return localStorage.getItem(SEEN_NOTICE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function subscribeToSeenNotice(listener: () => void): () => void {
  // Another tab that opens the board marks the notice seen here too.
  const onStorage = (event: StorageEvent) => {
    if (event.key !== SEEN_NOTICE_STORAGE_KEY) return;
    seenNoticeId = event.newValue;
    listener();
  };
  seenListeners.add(listener);
  window.addEventListener("storage", onStorage);
  return () => {
    seenListeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** Record that the user saw a notice on the board, which clears the sidebar dot. */
export function markSystemNoticeSeen(noticeId: string): void {
  if (seenNoticeId === noticeId) return;
  seenNoticeId = noticeId;
  try {
    localStorage.setItem(SEEN_NOTICE_STORAGE_KEY, noticeId);
  } catch {
    // The dot still clears for this page when storage is unavailable.
  }
  seenListeners.forEach((listener) => listener());
}

/** The id of the last notice the user saw on the board, or ``null``. */
export function useSeenSystemNoticeId(): string | null {
  return useSyncExternalStore(
    subscribeToSeenNotice,
    () => seenNoticeId,
    () => seenNoticeId,
  );
}
