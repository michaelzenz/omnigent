import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TaskDashboard, TaskItemSummary } from "@/lib/agentTasksApi";
import { TaskItemsPanel } from "./TaskCardWorkers";

const resolveMutateAsync = vi.fn();

vi.mock("@/hooks/useAgentTasks", () => ({
  useResolveTaskItem: vi.fn(() => ({ mutateAsync: resolveMutateAsync, isPending: false })),
  useAssignTaskItemWorker: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useCreateTaskItem: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
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

describe("human action task items", () => {
  afterEach(() => {
    cleanup();
    resolveMutateAsync.mockClear();
  });

  it("renders badge and description with Done/Dismiss and no worker controls", () => {
    render(
      <TaskItemsPanel
        taskId="task-1"
        dashboard={dashboardWith({ inbox_items: [HUMAN_ACTION_ITEM] })}
        selectedWorkerId={null}
      />,
    );

    expect(screen.getByText("human action")).toBeInTheDocument();
    expect(screen.getByText(HUMAN_ACTION_ITEM.title)).toBeInTheDocument();
    expect(screen.getByText(/IAM console/)).toBeInTheDocument();
    expect(screen.queryByText("Change worker")).not.toBeInTheDocument();
    expect(screen.queryByText("Accept")).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Mark human action done"));
    expect(resolveMutateAsync).toHaveBeenCalledWith({
      taskItemId: "ha-1",
      resolution: "mark_done",
    });

    fireEvent.click(screen.getByLabelText("Dismiss human action"));
    expect(resolveMutateAsync).toHaveBeenCalledWith({
      taskItemId: "ha-1",
      resolution: "reject_item",
    });
  });

  it("renders recently done human actions without action buttons", () => {
    render(
      <TaskItemsPanel
        taskId="task-1"
        dashboard={dashboardWith({
          recent_done_items: {
            all: [{ ...HUMAN_ACTION_ITEM, state: "done" }],
            by_worker: {},
          },
        })}
        selectedWorkerId={null}
      />,
    );

    fireEvent.click(screen.getByText("Recently done (1)"));
    expect(screen.getByText(HUMAN_ACTION_ITEM.title)).toBeInTheDocument();
    expect(screen.getByText("human action")).toBeInTheDocument();
    expect(screen.queryByLabelText("Mark human action done")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Dismiss human action")).not.toBeInTheDocument();
  });

  it("still renders work items with the worker ack editor", () => {
    const workItem: TaskItemSummary = {
      ...HUMAN_ACTION_ITEM,
      id: "work-1",
      title: "Regular work item",
      instructions: "Do the thing",
      kind: "work",
    };
    render(
      <TaskItemsPanel
        taskId="task-1"
        dashboard={dashboardWith({ inbox_items: [workItem] })}
        selectedWorkerId={null}
      />,
    );

    expect(screen.queryByText("human action")).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("Do the thing")).toBeInTheDocument();
  });
});
