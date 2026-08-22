import { cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { TaskCard } from "./TaskCard";
import { TaskCardWorkers } from "./TaskCardWorkers";
import { PuppyGardenChatProvider } from "./PuppyGardenChatContext";
import { INBOX_LANE_ID } from "./workerLaneStorage";

vi.mock("@/hooks/useAgentTasks", () => ({
  useTaskDashboard: vi.fn(),
  useSecretaryProfile: vi.fn(() => ({ data: { model: "composer-2.5" } })),
  useResolveTaskItem: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useUpdateTaskItem: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useStopTaskItem: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useRemoveTaskItem: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useRetryTaskItem: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useUpdateAgentTaskManagerRole: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
  useAcceptAgentTaskPackage: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
  useRejectAgentTaskPackage: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
  useInitializeWorker: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  })),
  useDeleteTaskAsset: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
}));

vi.mock("@/hooks/useRoleProfiles", () => ({
  useRoleProfiles: vi.fn(() => ({
    data: [{ role: "manager:default", title: "Task manager (default)" }],
  })),
}));

import { useDeleteTaskAsset, useTaskDashboard } from "@/hooks/useAgentTasks";

const mockedDashboard = vi.mocked(useTaskDashboard);

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PuppyGardenChatProvider>
          <TaskCard
            taskId="task-1"
            title="Land PR #123"
            description="Fix upload retries"
            state="active"
            managerRoleKey="manager:default"
          />
        </PuppyGardenChatProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderWorkers(ui: React.ReactElement) {
  return render(<PuppyGardenChatProvider>{ui}</PuppyGardenChatProvider>);
}

afterEach(cleanup);

describe("TaskCard", () => {
  it("renders unassigned inbox and worker lanes with assets panel", () => {
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
            id: "item-unassigned",
            title: "Pick a worker",
            description: null,
            instructions: "Route this to someone",
            internal_note: null,
            state: "pending",
            worker_id: null,
            created_at: 1,
            updated_at: null,
          },
        ],
        reconcile_queue_count: 0,
        assets: [],
        workers: [
          {
            worker_id: "worker-1",
            kind: "managed",
            target_id: null,
            state: "active",
            situation: "Running: Investigate failure",
            rows: [
              {
                kind: "execution",
                default_folded: false,
                sort_at: 2,
                execution: {
                  id: "exec-1",
                  task_item_id: "item-running",
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
              },
              {
                kind: "execution",
                default_folded: true,
                sort_at: 1,
                execution: {
                  id: "exec-done",
                  task_item_id: "item-done",
                  event_title: "Earlier fix",
                  status: "succeeded",
                  result_summary: "ok",
                  error: null,
                  conversation_id: "worker-session-old",
                  attempt_no: 1,
                  assigned_at: 1,
                  started_at: 1,
                  finished_at: 1,
                },
              },
            ],
            executions: [],
          },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useTaskDashboard>);

    renderCard();
    expect(screen.getByTestId("task-card-body")).toHaveAttribute("data-sparse", "false");

    expect(screen.getByText("Inbox")).toBeInTheDocument();
    expect(screen.getByTestId(`worker-lane-${INBOX_LANE_ID}`)).toHaveAttribute(
      "data-expanded",
      "true",
    );
    expect(screen.getByTestId("worker-row-item:item-unassigned")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Route this to someone")).toBeInTheDocument();
    expect(screen.getByText("Workers")).toBeInTheDocument();
    expect(screen.getByTestId("worker-lane-name-worker-1")).toHaveTextContent(
      "Task worker (default)",
    );
    // The lane has no session yet, so it offers the role choice, not Chat.
    expect(screen.getByTestId("worker-lane-role-worker-1")).toBeInTheDocument();
    expect(screen.getByTestId("worker-lane-activate-worker-1")).toHaveTextContent("Activate");
    expect(screen.queryByTestId("worker-lane-chat-worker-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("task-card-assets")).toBeInTheDocument();
    expect(screen.queryByText("Sessions")).not.toBeInTheDocument();
    expect(screen.getByTestId("worker-lane-worker-1")).toHaveAttribute("data-expanded", "false");
    fireEvent.click(screen.getByTestId("worker-lane-toggle-worker-1"));
    expect(screen.getByText("Investigate failure")).toBeInTheDocument();
  });

  it("opens an asset by clicking its title and removes it via the X button", () => {
    const removeMutate = vi.fn();
    vi.mocked(useDeleteTaskAsset).mockReturnValue({
      mutate: removeMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteTaskAsset>);

    mockedDashboard.mockReturnValue({
      data: {
        task: {
          id: "task-1",
          title: "Land PR #123",
          description: "Fix upload retries",
          state: "active",
          manager_conversation_id: "mgr-session",
        },
        derived: { has_running_workers: false },
        inbox_items: [],
        reconcile_queue_count: 0,
        assets: [
          {
            id: 1,
            kind: "url",
            title: "PR #123",
            url: "https://github.com/example/repo/pull/123",
            created_at: 1,
          },
        ],
        workers: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useTaskDashboard>);

    renderCard();

    // The title is the open affordance: clicking it follows the link.
    const title = screen.getByText("PR #123");
    expect(title.tagName).toBe("A");
    expect(title).toHaveAttribute("href", "https://github.com/example/repo/pull/123");

    // The X button detaches the asset.
    fireEvent.click(screen.getByTestId("task-asset-remove-1"));
    expect(removeMutate).toHaveBeenCalledWith(1);
  });

  it("expands a worker lane and folds finished rows by default", async () => {
    renderWorkers(
      <TaskCardWorkers
        taskId="task-1"
        inboxItems={[]}
        workers={[
          {
            worker_id: "worker-1",
            kind: "managed",
            target_id: "worker-session",
            state: "idle",
            situation: "Idle",
            rows: [
              {
                kind: "item",
                default_folded: false,
                sort_at: 3,
                item: {
                  id: "item-q",
                  title: "Queued task",
                  description: null,
                  instructions: "Do the thing",
                  internal_note: null,
                  state: "queued",
                  worker_id: "worker-1",
                  created_at: 3,
                  updated_at: null,
                },
              },
              {
                kind: "execution",
                default_folded: true,
                sort_at: 1,
                execution: {
                  id: "exec-done",
                  task_item_id: "item-done",
                  event_title: "Done task",
                  status: "succeeded",
                  result_summary: "ok",
                  error: null,
                  conversation_id: null,
                  attempt_no: 1,
                  assigned_at: 1,
                  started_at: 1,
                  finished_at: 1,
                },
              },
            ],
            executions: [],
          },
        ]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("worker-lane-worker-1")).toHaveAttribute("data-expanded", "true");
    });
    expect(screen.getByDisplayValue("Do the thing")).toBeInTheDocument();
    expect(screen.getByTestId("worker-row-exec:exec-done")).toHaveAttribute("data-folded", "true");
    fireEvent.click(within(screen.getByTestId("worker-row-exec:exec-done")).getByRole("button"));
    expect(screen.getByTestId("worker-row-exec:exec-done")).toHaveAttribute("data-folded", "false");
  });

  it("always scrolls the worker lane list", () => {
    const workers = Array.from({ length: 5 }, (_, index) => ({
      worker_id: `worker-${index}`,
      kind: "managed",
      target_id: null,
      state: "new" as const,
      situation: "New",
      rows: [],
      executions: [],
    }));

    renderWorkers(<TaskCardWorkers taskId="task-many" inboxItems={[]} workers={workers} />);

    expect(screen.getByTestId("task-card-workers").className).toContain("overflow-y-auto");
    expect(screen.getByTestId("task-card-workers").className).toContain("flex-1");
  });

  it("shows retry and remove on parked items", async () => {
    renderWorkers(
      <TaskCardWorkers
        taskId="task-1"
        inboxItems={[]}
        workers={[
          {
            worker_id: "worker-1",
            kind: "managed",
            target_id: "worker-session",
            state: "idle",
            situation: "Idle",
            rows: [
              {
                kind: "item",
                default_folded: false,
                sort_at: 2,
                item: {
                  id: "item-interrupted",
                  title: "Interrupted task",
                  description: null,
                  instructions: "Finish the docs",
                  internal_note: null,
                  state: "interrupted",
                  worker_id: "worker-1",
                  queue_item_id: "queue-1",
                  created_at: 2,
                  updated_at: 2,
                },
              },
            ],
            executions: [],
          },
        ]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("Retry dispatch")).toBeInTheDocument();
      expect(screen.getByLabelText("Remove item from queue")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("worker-row-item:fixture-item-done-hidden"),
    ).not.toBeInTheDocument();
  });

  it("collapses inbox lane when the header is toggled", () => {
    mockedDashboard.mockReturnValue({
      data: {
        task: {
          id: "task-1",
          title: "Land PR #123",
          description: null,
          state: "active",
          manager_conversation_id: null,
        },
        derived: { has_running_workers: false },
        inbox_items: [
          {
            id: "item-unassigned",
            title: "Pick a worker",
            description: null,
            instructions: "Route this to someone",
            internal_note: null,
            state: "pending",
            worker_id: null,
            created_at: 1,
            updated_at: null,
          },
        ],
        reconcile_queue_count: 0,
        assets: [],
        workers: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useTaskDashboard>);

    renderCard();
    fireEvent.click(screen.getByTestId(`worker-lane-toggle-${INBOX_LANE_ID}`));
    expect(screen.getByTestId(`worker-lane-${INBOX_LANE_ID}`)).toHaveAttribute(
      "data-expanded",
      "false",
    );
    expect(screen.queryByTestId("worker-row-item:item-unassigned")).not.toBeInTheDocument();
  });

  it("applies minimum body height when the dashboard is sparse", () => {
    mockedDashboard.mockReturnValue({
      data: {
        task: {
          id: "task-empty",
          title: "Empty task",
          description: null,
          state: "pending",
          manager_conversation_id: null,
        },
        derived: { has_running_workers: false },
        inbox_items: [],
        reconcile_queue_count: 0,
        assets: [],
        workers: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useTaskDashboard>);

    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <MemoryRouter>
          <PuppyGardenChatProvider>
            <TaskCard
              taskId="task-empty"
              title="Empty task"
              description={null}
              state="pending"
              managerRoleKey="manager:default"
            />
          </PuppyGardenChatProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByTestId("task-card-body")).toHaveAttribute("data-sparse", "true");
    expect(screen.getByTestId("task-card-body").className).toContain("max-h-[480px]");
    expect(screen.getByText("No assets yet.")).toBeInTheDocument();
  });
});
