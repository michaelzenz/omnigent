import type { AgentTask } from "@/hooks/useGlobalHubTasks";

/** An active task with test defaults; ``fields`` override them. */
export function agentTask(id: string, fields: Partial<AgentTask> = {}): AgentTask {
  return {
    id,
    manager_id: null,
    title: `Task ${id}`,
    goal: "Fix the S3 retries",
    description: null,
    state: "active",
    priority: 2,
    queue_rank: 0,
    created_at: 1_800_000_000,
    updated_at: null,
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
