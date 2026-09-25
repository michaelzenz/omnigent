import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TaskDashboard, TaskItemSummary } from "@/lib/agentTasksApi";
import type * as agentTasksHooks from "@/hooks/useAgentTasks";
import { TaskItemsPanel } from "./TaskCardWorkers";

const closeMutateAsync = vi.fn();
const removeMutateAsync = vi.fn();

vi.mock("@/hooks/useAgentTasks", async (importOriginal) => ({
  ...(await importOriginal<typeof agentTasksHooks>()),
  useCloseTaskItem: vi.fn(() => ({ mutateAsync: closeMutateAsync, isPending: false })),
  useUpdateTaskItem: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useAssignTaskItemWorker: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useCreateTaskItem: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useStopTaskItem: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useRemoveTaskItem: vi.fn(() => ({ mutateAsync: removeMutateAsync, isPending: false })),
  useRetryTaskItem: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useUntrackWorker: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/useWorkerProviders", () => ({
  useWorkerProviders: vi.fn(() => ({ data: [] })),
}));

const HUMAN_ACTION_ITEM: TaskItemSummary = {
  id: "ha-1",
  title: "Rotate the AWS access key",
  description: "Only you have IAM console access. Create a new key, then mark this done.",
  instructions: null,
  internal_note: null,
  state: "pending",
  worker_id: null,
  kind: "human_action",
  created_at: 10,
  updated_at: null,
};

function dashboardWith(overrides: Partial<TaskDashboard>): TaskDashboard {
  return {
    task: {
      id: "task-1",
      title: "Ship it",
      description: null,
      state: "active",
      manager_id: null,
      manager_conversation_id: null,
    },
    derived: { has_running_workers: false },
    inbox_items: [],
    reconcile_queue_count: 0,
    assets: [],
    workers: [],
    ...overrides,
  };
}

// Provider so any hook that slips past the vi.mock stubs still resolves.
function renderPanel(dashboard: TaskDashboard) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <TaskItemsPanel taskId="task-1" dashboard={dashboard} selectedWorkerId={null} />
    </QueryClientProvider>,
  );
}

describe("human action task items", () => {
  afterEach(() => {
    cleanup();
    closeMutateAsync.mockClear();
    removeMutateAsync.mockClear();
  });

  it("renders badge and description with Done/Dismiss and no worker controls once expanded", () => {
    renderPanel(dashboardWith({ inbox_items: [HUMAN_ACTION_ITEM] }));

    // Items ship shrunk: title + human-action badge only.
    expect(screen.getByText("human action")).toBeInTheDocument();
    expect(screen.getByText(HUMAN_ACTION_ITEM.title)).toBeInTheDocument();
    expect(screen.queryByText(/IAM console/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Rotate the AWS access key/ }));

    expect(screen.getByText(/IAM console/)).toBeInTheDocument();
    expect(screen.queryByText("Change worker")).not.toBeInTheDocument();
    expect(screen.queryByText("Accept")).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Mark human action done"));
    expect(closeMutateAsync).toHaveBeenCalledWith("ha-1");

    fireEvent.click(screen.getByLabelText("Dismiss human action"));
    expect(removeMutateAsync).toHaveBeenCalledWith({ taskItemId: "ha-1" });
  });

  it("renders recently done human actions without action buttons", () => {
    renderPanel(
      dashboardWith({
        recent_done_items: {
          all: [{ ...HUMAN_ACTION_ITEM, state: "done" }],
          by_worker: {},
        },
      }),
    );

    fireEvent.click(screen.getByText("Recently done (1)"));
    fireEvent.click(screen.getByRole("button", { name: /Rotate the AWS access key/ }));
    expect(screen.getByText(HUMAN_ACTION_ITEM.title)).toBeInTheDocument();
    expect(screen.getByText("human action")).toBeInTheDocument();
    expect(screen.queryByLabelText("Mark human action done")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Dismiss human action")).not.toBeInTheDocument();
  });

  it("still renders work items with the worker ack editor once expanded", () => {
    const workItem: TaskItemSummary = {
      ...HUMAN_ACTION_ITEM,
      id: "work-1",
      title: "Regular work item",
      instructions: "Do the thing",
      kind: "work",
    };
    renderPanel(dashboardWith({ inbox_items: [workItem] }));

    fireEvent.click(screen.getByRole("button", { name: /Regular work item/ }));

    expect(screen.queryByText("human action")).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("Do the thing")).toBeInTheDocument();
  });

  it("ships work items shrunk to title + state badge and expands on click", () => {
    const workItem: TaskItemSummary = {
      ...HUMAN_ACTION_ITEM,
      id: "work-shrink",
      title: "Shrinkable work item",
      instructions: "Do the thing",
      kind: "work",
    };
    renderPanel(dashboardWith({ inbox_items: [workItem] }));

    const head = screen.getByRole("button", { name: /Shrinkable work item/ });
    expect(head).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByDisplayValue("Do the thing")).not.toBeInTheDocument();

    fireEvent.click(head);
    expect(screen.getByRole("button", { name: /Shrinkable work item/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByDisplayValue("Do the thing")).toBeInTheDocument();
  });
});
