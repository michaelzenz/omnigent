/** Static glossary entries (manager templates are loaded from the API). */
export type RoleDefaultsKind = "per_user" | "per_task";

export interface RoleCatalogEntry {
  id: string;
  title: string;
  definition: string;
  defaultsKind: RoleDefaultsKind;
  profileRoleId?: string;
}

export const STATIC_ROLE_CATALOG: RoleCatalogEntry[] = [
  {
    id: "broker",
    title: "Task broker",
    defaultsKind: "per_user",
    profileRoleId: "broker",
    definition:
      "You are a task broker. Follow docs/agent-tasks/TASK_BROKER.md. When routing stalls, list the ambiguous inbox, auto-route confident active-task matches via batch-resolve, reconcile onto pending packages when appropriate, and create new pending task packages for uncertain cases.",
  },
  {
    id: "secretary",
    title: "Task secretary",
    defaultsKind: "per_user",
    profileRoleId: "secretary",
    definition:
      "You are the task secretary of the PuppyGarden task system. You are a lightweight assistant: you remember the available endpoints and answer the user's questions about the task system. Read docs/agent-tasks/API_REFERENCE.md when you need to recall an endpoint's shape or parameters. Do not triage events, create packages, or dispatch workers — those are the broker's job.",
  },
];

export const MANAGER_ROLE_DEFINITION =
  "You are a task manager for one managed task. Follow docs/agent-tasks/TASK_MANAGER.md. Triage routed events into task items, reconcile the backlog, and dispatch workers after user approval.";

export const WORKER_ROLE_DEFINITION =
  "You are a task worker. Follow docs/agent-tasks/TASK_WORKER.md. Execute the assigned task item instructions, report progress, and finish when done.";
