// Session label marking a GlobalHub role session; mirrors the backend's
// `omnigent.agent_tasks.session_labels`. Role sessions are background agents
// whose output lands on the GlobalHub board, not in a chat the user reads.
export const ROLE_LABEL_KEY = "omnigent.role";

const BACKGROUND_ROLE_VALUES: ReadonlySet<string> = new Set([
  "task_broker",
  "task_manager",
  "task_custodian",
]);

/** Whether a session is a GlobalHub role: the broker, a manager, or the custodian. */
export function isBackgroundRoleSession(labels: Record<string, string> | undefined): boolean {
  const role = labels?.[ROLE_LABEL_KEY];
  return role !== undefined && BACKGROUND_ROLE_VALUES.has(role);
}
