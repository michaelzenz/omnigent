import type { FyiCluster, FyiEvent } from "@/hooks/useGlobalHubFyi";
import type { SystemNotice, SystemNoticeInput } from "@/hooks/useGlobalHubNotice";
import type {
  AgentTaskAsset,
  AgentTaskCard,
  AgentTaskWorker,
  BoardSession,
} from "@/hooks/useGlobalHubTasks";

/** An active task card with no workers or assets; ``fields`` override the defaults. */
export function agentTask(id: string, fields: Partial<AgentTaskCard> = {}): AgentTaskCard {
  return {
    id,
    manager_id: null,
    title: `Task ${id}`,
    description: null,
    state: "active",
    priority: 2,
    queue_rank: 0,
    created_at: 1_800_000_000,
    updated_at: null,
    workers: [],
    assets: [],
    ...fields,
  };
}

/** An idle session; ``fields`` override the defaults. */
export function boardSession(fields: Partial<BoardSession> = {}): BoardSession {
  return {
    title: "Session",
    status: "idle",
    pending_elicitations_count: 0,
    background_work_running: false,
    blocked_on: null,
    updated_at: 1_800_000_000,
    ...fields,
  };
}

/** A worker on task ``t1`` whose session ``s-<id>`` is idle; the arguments override that. */
export function taskWorker(
  id: string,
  fields: Partial<AgentTaskWorker> = {},
  session: Partial<BoardSession> = {},
): AgentTaskWorker {
  return {
    id,
    task_id: "t1",
    session_id: `s-${id}`,
    title: null,
    created_at: 1_800_000_000,
    updated_at: null,
    session: boardSession({ title: `Session ${id}`, ...session }),
    ...fields,
  };
}

/** A link on task ``t1`` in the "other" category; ``fields`` override the defaults. */
export function taskAsset(id: string, fields: Partial<AgentTaskAsset> = {}): AgentTaskAsset {
  return {
    id,
    task_id: "t1",
    category: "other",
    title: `Asset ${id}`,
    url: `https://example.com/${id}`,
    related_session_ids: [],
    created_at: 1_800_000_000,
    updated_at: null,
    ...fields,
  };
}

/** An active Markdown notice with no actions; ``fields`` override the defaults. */
export function systemNotice(id: string, fields: Partial<SystemNotice> = {}): SystemNotice {
  return {
    id,
    title: `Notice ${id}`,
    body_kind: "markdown",
    body: null,
    actions: [],
    state: "active",
    publisher_session_id: null,
    publisher_label: null,
    created_at: 1_800_000_000,
    updated_at: null,
    resolved_at: null,
    ...fields,
  };
}

/** A toggle without a label or group; ``fields`` override the defaults. */
export function noticeInput(
  id: string,
  fields: Partial<SystemNoticeInput> = {},
): SystemNoticeInput {
  return {
    id,
    kind: "toggle",
    label: null,
    group: null,
    placeholder: null,
    default: null,
    required: null,
    ...fields,
  };
}

/** A pending FYI cluster with no events; ``fields`` override the defaults. */
export function fyiCluster(id: string, fields: Partial<FyiCluster> = {}): FyiCluster {
  return {
    id,
    headline: `Cluster ${id}`,
    rationale: null,
    created_at: 1_800_000_000,
    events: [],
    ...fields,
  };
}

/** A finished turn of session ``s-<id>``; ``fields`` override the defaults. */
export function fyiEvent(id: string, fields: Partial<FyiEvent> = {}): FyiEvent {
  return {
    id,
    event_type: "session.turn.finished",
    title: `Session turn finished: Session ${id}`,
    source_session_id: `s-${id}`,
    source_key: null,
    created_at: 1_800_000_000,
    ...fields,
  };
}

/** A JSON response as the server sends it. */
export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
