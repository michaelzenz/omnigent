import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { TaskCard } from "./TaskCard";
import { TaskCardWork } from "./TaskCardWork";

vi.mock("@/hooks/useAgentTasks", () => ({
  useTaskDashboard: vi.fn(),
  useSecretaryProfile: vi.fn(() => ({ data: { model: "composer-2.5" } })),
  useResolveTaskItem: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
}));

vi.mock("@/hooks/useAvailableAgents", () => ({
  useAvailableAgents: vi.fn(() => ({
    data: [{ id: "worker-1", name: "ci-fixer", display_name: "CI Fixer", harness: null, skills: [] }],
  })),
}));

import { useTaskDashboard } from "@/hooks/useAgentTasks";

const mockedDashboard = vi.mocked(useTaskDashboard);

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TaskCard
          taskId="task-1"
          title="Land PR #123"
          description="Fix upload retries"
          state="active"
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

function workItemTitles(workerAgentId: string): string[] {
  const list =
    screen.queryByTestId(`worker-items-scroll-${workerAgentId}`) ??
    screen.getByTestId(`worker-items-${workerAgentId}`);
  return within(list)
    .getAllByRole("listitem")
    .map((item) => item.querySelector("p")?.textContent?.trim() ?? "");
}

describe("TaskCard", () => {
  it("renders inbox items and grouped work", () => {
    mockedDashboard.mockReturnValue({
      data: {
        task: {
          id: "task-1",
          title: "Land PR #123",
          description: "Fix upload retries",
          state: "active",
          manager_conversation_id: "mgr-session",
        },
        derived: { has_running_workers: true },
        inbox_items: [
          {
            id: "item-1",
            title: "Retry CI",
            instructions: "Rerun checks",
            state: "awaiting_user_ack",
            worker_agent_id: "worker-1",
            model: "composer-2.5",
            host_id: null,
            workspace: null,
            harness: null,
            created_at: 1,
            updated_at: null,
          },
          {
            id: "item-2",
            title: "Update docs",
            instructions: "Refresh README after merge",
            state: "awaiting_user_ack",
            worker_agent_id: "worker-1",
            model: "composer-2.5",
            host_id: null,
            workspace: null,
            harness: null,
            created_at: 2,
            updated_at: null,
          },
        ],
        reconcile_queue_count: 0,
        workers: [
          {
            worker_agent_id: "worker-1",
            executions: [
              {
                id: "exec-1",
                task_item_id: "item-running",
                event_id: "evt-1",
                event_title: "Investigate failure",
                status: "running",
                result_summary: null,
                error: null,
                conversation_id: "worker-session",
                attempt_no: 1,
                assigned_at: 2,
                started_at: 2,
                finished_at: null,
              },
            ],
          },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useTaskDashboard>);

    renderCard();

    expect(screen.getByText("Land PR #123")).toBeInTheDocument();
    expect(screen.getByText("Inbox")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-item-item-1")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-item-item-2")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Rerun checks")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Refresh README after merge")).toBeInTheDocument();
    expect(screen.getByText("Work")).toBeInTheDocument();
    expect(screen.getByText("CI Fixer")).toBeInTheDocument();
    expect(screen.getAllByText("Investigate failure").length).toBeGreaterThan(0);
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("Manager")).toBeInTheDocument();
  });

  it("scrolls work when more than two worker groups are present", () => {
    render(
      <TaskCardWork
        agents={[
          { id: "w1", name: "ci-fixer", display_name: "CI Fixer", description: null, harness: null, skills: [] },
          { id: "w2", name: "reviewer", display_name: "Reviewer", description: null, harness: null, skills: [] },
          { id: "w3", name: "docs", display_name: "Docs", description: null, harness: null, skills: [] },
        ]}
        workers={[
          {
            worker_agent_id: "w1",
            executions: [
              {
                id: "e1",
                task_item_id: "item-1",
                event_id: "ev1",
                event_title: "Run checks",
                status: "running",
                result_summary: null,
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 1,
                started_at: 1,
                finished_at: null,
              },
            ],
          },
          {
            worker_agent_id: "w2",
            executions: [
              {
                id: "e2",
                task_item_id: "item-2",
                event_id: "ev2",
                event_title: "Review diff",
                status: "queued",
                result_summary: null,
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 2,
                started_at: null,
                finished_at: null,
              },
            ],
          },
          {
            worker_agent_id: "w3",
            executions: [
              {
                id: "e3",
                task_item_id: "item-3",
                event_id: "ev3",
                event_title: "Update docs",
                status: "succeeded",
                result_summary: "Done",
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 3,
                started_at: 3,
                finished_at: 4,
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByTestId("task-card-work-scroll")).toBeInTheDocument();
  });

  it("scrolls task items within a worker when more than two are present", () => {
    render(
      <TaskCardWork
        agents={[
          { id: "w1", name: "task-worker", display_name: "Task Worker", description: null, harness: null, skills: [] },
        ]}
        workers={[
          {
            worker_agent_id: "w1",
            executions: [
              {
                id: "e1",
                task_item_id: "item-1",
                event_id: "ev1",
                event_title: "Investigate failure",
                status: "running",
                result_summary: null,
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 1,
                started_at: 1,
                finished_at: null,
              },
              {
                id: "e2",
                task_item_id: "item-2",
                event_id: "ev2",
                event_title: "Rerun upload job",
                status: "queued",
                result_summary: null,
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 2,
                started_at: null,
                finished_at: null,
              },
              {
                id: "e3",
                task_item_id: "item-3",
                event_id: "ev3",
                event_title: "Verify green checks",
                status: "succeeded",
                result_summary: "All checks passed",
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 3,
                started_at: 3,
                finished_at: 4,
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByTestId("worker-items-scroll-w1")).toBeInTheDocument();
  });

  it("renders worker task items in fifo receive order within each status bucket", () => {
    render(
      <TaskCardWork
        agents={[
          { id: "w1", name: "task-worker", display_name: "Task Worker", description: null, harness: null, skills: [] },
        ]}
        workers={[
          {
            worker_agent_id: "w1",
            executions: [
              {
                id: "d2",
                task_item_id: "item-d2",
                event_id: "ev-d2",
                event_title: "Done second",
                status: "succeeded",
                result_summary: "ok",
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 50,
                started_at: 50,
                finished_at: 55,
              },
              {
                id: "q2",
                task_item_id: "item-q2",
                event_id: "ev-q2",
                event_title: "Queue second",
                status: "queued",
                result_summary: null,
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 40,
                started_at: null,
                finished_at: null,
              },
              {
                id: "r2",
                task_item_id: "item-r2",
                event_id: "ev-r2",
                event_title: "Running second",
                status: "running",
                result_summary: null,
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 20,
                started_at: 20,
                finished_at: null,
              },
              {
                id: "d1",
                task_item_id: "item-d1",
                event_id: "ev-d1",
                event_title: "Done first",
                status: "succeeded",
                result_summary: "ok",
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 45,
                started_at: 45,
                finished_at: 48,
              },
              {
                id: "q1",
                task_item_id: "item-q1",
                event_id: "ev-q1",
                event_title: "Queue first",
                status: "queued",
                result_summary: null,
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 35,
                started_at: null,
                finished_at: null,
              },
              {
                id: "r1",
                task_item_id: "item-r1",
                event_id: "ev-r1",
                event_title: "Running first",
                status: "running",
                result_summary: null,
                error: null,
                conversation_id: null,
                attempt_no: 1,
                assigned_at: 15,
                started_at: 15,
                finished_at: null,
              },
            ],
          },
        ]}
      />,
    );

    expect(workItemTitles("w1")).toEqual([
      "Running first",
      "Running second",
      "Queue first",
      "Queue second",
      "Done first",
      "Done second",
    ]);
  });
});
