import type { AgentTaskSummary, TaskDashboard } from "@/lib/agentTasksApi";
import { FIXTURE_ACTIVE_TASK_ID, FIXTURE_PENDING_TASK_ID } from "./puppyGardenFixtureMode";

const NOW = Math.floor(Date.now() / 1000);

export const FIXTURE_TASK_LIST: AgentTaskSummary[] = [
  {
    id: FIXTURE_PENDING_TASK_ID,
    title: "Triage: Slack thread about deploy",
    description: "Broker package waiting for a manager",
    state: "pending",
    agent_profile_id: "fixture-manager",
    manager_conversation_id: null,
  },
  {
    id: FIXTURE_ACTIVE_TASK_ID,
    title: "Land PR #123",
    description: "Fix upload retries and get CI green",
    state: "active",
    agent_profile_id: "fixture-manager",
    manager_conversation_id: "fixture-mgr-session",
  },
];

export function buildFixtureDashboard(taskId: string): TaskDashboard | null {
  if (taskId === FIXTURE_PENDING_TASK_ID) {
    return {
      task: {
        id: FIXTURE_PENDING_TASK_ID,
        title: "Triage: Slack thread about deploy",
        description: "Broker package waiting for a manager",
        state: "pending",
        manager_conversation_id: null,
      },
      derived: { has_running_workers: false },
      inbox_items: [
        {
          id: "fixture-inbox-1",
          title: "Route deploy thread",
          description: "Customer asked whether the hotfix shipped",
          instructions: "Read the Slack thread and propose a task",
          internal_note: null,
          state: "awaiting_user_ack",
          worker_id: null,
          queue_item_id: "fixture-queue-inbox-1",
          created_at: NOW - 120,
          updated_at: null,
        },
      ],
      reconcile_queue_count: 2,
      assets: [],
      workers: [],
    };
  }

  if (taskId !== FIXTURE_ACTIVE_TASK_ID) {
    return null;
  }

  return {
    task: {
      id: FIXTURE_ACTIVE_TASK_ID,
      title: "Land PR #123",
      description: "Fix upload retries and get CI green",
      state: "active",
      manager_conversation_id: "fixture-mgr-session",
    },
    derived: { has_running_workers: true },
    inbox_items: [],
    reconcile_queue_count: 0,
    assets: [
      {
        id: 1,
        kind: "url",
        title: "PR #123",
        url: "https://github.com/example/repo/pull/123",
        created_at: NOW - 3600,
      },
    ],
    workers: [
      {
        worker_id: "fixture-worker-1",
        profile_id: "fixture-worker-profile-1",
        session_id: "fixture-worker-session",
        state: "active",
        situation: "Running: Investigate CI failure",
        rows: [
          {
            kind: "execution",
            default_folded: false,
            sort_at: NOW - 30,
            execution: {
              id: "fixture-exec-running",
              task_item_id: "fixture-item-running",
              event_title: "Investigate CI failure",
              item: {
                id: "fixture-item-running",
                title: "Investigate CI failure",
                description: "Unit tests failed on upload path",
                instructions: "Read the CI log and fix the flaky assertion",
                internal_note: null,
                state: "running",
                worker_id: "fixture-worker-1",
                queue_item_id: "fixture-queue-running",
                created_at: NOW - 90,
                updated_at: NOW - 30,
              },
              status: "running",
              result_summary: null,
              error: null,
              conversation_id: "fixture-worker-session",
              attempt_no: 1,
              assigned_at: NOW - 60,
              started_at: NOW - 45,
              finished_at: null,
            },
          },
          {
            kind: "item",
            default_folded: false,
            sort_at: NOW - 20,
            item: {
              id: "fixture-item-queued",
              title: "Retry upload suite",
              description: null,
              instructions: "Re-run the upload integration tests after the fix",
              internal_note: null,
              state: "queued",
              worker_id: "fixture-worker-1",
              queue_item_id: "fixture-queue-queued",
              created_at: NOW - 20,
              updated_at: null,
            },
          },
          {
            kind: "item",
            default_folded: false,
            sort_at: NOW - 10,
            item: {
              id: "fixture-item-interrupted",
              title: "Docs follow-up",
              description: null,
              instructions: "Update the runbook with the new retry policy",
              internal_note: null,
              state: "interrupted",
              worker_id: "fixture-worker-1",
              queue_item_id: "fixture-queue-interrupted",
              created_at: NOW - 300,
              updated_at: NOW - 10,
            },
          },
          {
            kind: "item",
            default_folded: false,
            sort_at: NOW - 5,
            item: {
              id: "fixture-item-dispatch-failed",
              title: "Spawn review worker",
              description: null,
              instructions: "Ask the reviewer agent to scan the diff",
              internal_note: null,
              state: "dispatch_failed",
              worker_id: "fixture-worker-1",
              queue_item_id: "fixture-queue-dispatch-failed",
              created_at: NOW - 200,
              updated_at: NOW - 5,
            },
          },
          {
            kind: "item",
            default_folded: true,
            sort_at: NOW - 400,
            item: {
              id: "fixture-item-done-hidden",
              title: "Should not render",
              description: "Done items are filtered out",
              instructions: "Hidden on purpose",
              internal_note: null,
              state: "done",
              worker_id: "fixture-worker-1",
              queue_item_id: "fixture-queue-done",
              created_at: NOW - 400,
              updated_at: NOW - 350,
            },
          },
          {
            kind: "execution",
            default_folded: true,
            sort_at: NOW - 500,
            execution: {
              id: "fixture-exec-done",
              task_item_id: "fixture-item-done-exec",
              event_title: "Earlier lint fix",
              item: {
                id: "fixture-item-done-exec",
                title: "Earlier lint fix",
                description: null,
                instructions: "Fix import order",
                internal_note: null,
                state: "done",
                worker_id: "fixture-worker-1",
                queue_item_id: null,
                created_at: NOW - 600,
                updated_at: NOW - 500,
              },
              status: "succeeded",
              result_summary: "Lint clean",
              error: null,
              conversation_id: "fixture-worker-session-old",
              attempt_no: 1,
              assigned_at: NOW - 550,
              started_at: NOW - 540,
              finished_at: NOW - 500,
            },
          },
        ],
        executions: [],
      },
      {
        worker_id: "fixture-worker-2",
        profile_id: "fixture-worker-profile-2",
        session_id: null,
        state: "new",
        situation: "New",
        rows: [],
        executions: [],
      },
    ],
  };
}

export function cloneFixtureDashboards(): Map<string, TaskDashboard> {
  const map = new Map<string, TaskDashboard>();
  for (const task of FIXTURE_TASK_LIST) {
    const dashboard = buildFixtureDashboard(task.id);
    if (dashboard) {
      map.set(task.id, structuredClone(dashboard));
    }
  }
  return map;
}
