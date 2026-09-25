import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { TaskCard } from "./TaskCard";
import { TaskCardSidebar } from "./TaskCardAssets";
import { TaskCardWorkers } from "./TaskCardWorkers";
import { Pmv2ChatProvider } from "./Pmv2ChatContext";

vi.mock("@/hooks/useAgentTasks", () => ({
  useTaskDashboard: vi.fn(),
  useSecretaryProfile: vi.fn(() => ({ data: { model: "composer-2.5" } })),
  useFireTaskItem: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useCloseTaskItem: vi.fn(() => ({
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
  useMoveTaskToQueueEnd: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  usePatchAgentTask: vi.fn(() => ({
    mutateAsync: vi.fn(),
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
  useArchiveAgentTask: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  usePermanentlyDeleteAgentTask: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useAssignTaskItemWorker: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useCreateTaskItem: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useUntrackWorker: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useReassignWorker: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
}));

vi.mock("@/hooks/useRoleProfiles", () => ({
  useRoleProfiles: vi.fn(() => ({
    data: [{ role: "manager:default", title: "Task manager (default)" }],
  })),
}));

vi.mock("@/hooks/useWorkerProviders", () => ({
  useWorkerProviders: vi.fn(() => ({ data: [] })),
  useCreateWorkerProvider: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));

import { useDeleteTaskAsset, usePatchAgentTask, useTaskDashboard } from "@/hooks/useAgentTasks";

const mockedDashboard = vi.mocked(useTaskDashboard);

function renderCard(state = "active") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Pmv2ChatProvider>
          <TaskCard
            taskId="task-1"
            title="Land PR #123"
            description="Fix upload retries"
            state={state}
          />
        </Pmv2ChatProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderWorkers(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Pmv2ChatProvider>{ui}</Pmv2ChatProvider>
    </QueryClientProvider>,
  );
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
    expect(screen.getByTestId("task-card-body")).toBeInTheDocument();

    expect(screen.getByText("Task Items")).toBeInTheDocument();
    // Item rows ship collapsed — only the title is visible until expanded.
    expect(screen.getByText("Pick a worker")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Route this to someone")).not.toBeInTheDocument();
    // The sidebar rail defaults to the assets tab; the workers tab shows the count.
    expect(screen.getByText("No assets yet.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /workers/ })).toHaveTextContent("(1)");
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

  it("expands an item row and reveals recently-done rows behind its toggle", async () => {
    renderWorkers(
      <TaskCardWorkers
        taskId="task-1"
        inboxItems={[
          {
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
          {
            id: "item-done",
            title: "Earlier fix",
            description: null,
            instructions: null,
            internal_note: null,
            state: "done",
            worker_id: "worker-1",
            created_at: 1,
            updated_at: 1,
          },
        ]}
        workers={[]}
      />,
    );

    // Done rows ship behind a collapsed "Recently done" toggle.
    const doneToggle = screen.getByRole("button", { name: /Recently done \(1\)/ });
    expect(doneToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Earlier fix")).not.toBeInTheDocument();

    // Item rows ship collapsed too; expanding shows the editor content.
    fireEvent.click(screen.getByRole("button", { name: /Queued task/ }));
    expect(screen.getByText("Do the thing")).toBeInTheDocument();

    fireEvent.click(doneToggle);
    expect(screen.getByText("Earlier fix")).toBeInTheDocument();
  });

  it("renders every worker in the sidebar list", () => {
    const workers = Array.from({ length: 5 }, (_, index) => ({
      worker_id: `worker-${index}`,
      kind: "managed",
      target_id: null,
      state: "new" as const,
      title: `Worker ${index}`,
      situation: "New",
      rows: [],
      executions: [],
    }));

    renderWorkers(
      <TaskCardSidebar taskId="task-many" assets={[]} workers={workers} hostId={null} />,
    );

    // The worker list lives behind the sidebar's workers tab.
    fireEvent.click(screen.getByRole("tab", { name: /workers/ }));
    expect(screen.getByTestId("task-card-workers")).toBeInTheDocument();
    for (let index = 0; index < workers.length; index++) {
      expect(screen.getByText(`Worker ${index}`)).toBeInTheDocument();
    }
  });

  it("shows retry and remove on parked items", async () => {
    renderWorkers(
      <TaskCardWorkers
        taskId="task-1"
        inboxItems={[
          {
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
        ]}
        workers={[]}
      />,
    );

    // Parked rows ship collapsed; their actions render once expanded.
    fireEvent.click(screen.getByRole("button", { name: /Interrupted task/ }));
    expect(screen.getByLabelText("Retry dispatch")).toBeInTheDocument();
    expect(screen.getByLabelText("Remove item from queue")).toBeInTheDocument();
  });

  it("expands and re-collapses an item row from its header", () => {
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
    // Collapsed by default: only the title shows.
    expect(screen.queryByDisplayValue("Route this to someone")).not.toBeInTheDocument();

    const rowHead = screen.getByRole("button", { name: /Pick a worker/ });
    fireEvent.click(rowHead);
    expect(screen.getByDisplayValue("Route this to someone")).toBeInTheDocument();

    fireEvent.click(rowHead);
    expect(screen.queryByDisplayValue("Route this to someone")).not.toBeInTheDocument();
  });

  it("resolves the task via the tick button", async () => {
    const patchMutate = vi.fn().mockResolvedValue(undefined);
    vi.mocked(usePatchAgentTask).mockReturnValue({
      mutateAsync: patchMutate,
      isPending: false,
    } as unknown as ReturnType<typeof usePatchAgentTask>);
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
        inbox_items: [],
        reconcile_queue_count: 0,
        assets: [],
        workers: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useTaskDashboard>);

    renderCard();
    fireEvent.click(screen.getByTestId("task-card-resolve-task-1"));

    await waitFor(() => expect(patchMutate).toHaveBeenCalledWith({ state: "agent-resolved" }));
  });

  it("hides the resolve tick once the task is resolved", () => {
    mockedDashboard.mockReturnValue({
      data: {
        task: {
          id: "task-1",
          title: "Land PR #123",
          description: null,
          state: "agent-resolved",
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

    renderCard("agent-resolved");

    expect(screen.queryByTestId("task-card-resolve-task-1")).not.toBeInTheDocument();
  });
});
