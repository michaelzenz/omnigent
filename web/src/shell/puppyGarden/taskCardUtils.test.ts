import { describe, expect, it } from "vitest";
import {
  buildWorkerOptions,
  getFoldedExecutions,
  findExecution,
  isExecutionEditable,
  proposalHasEdits,
  sortExecutions,
  taskCardBodyStyle,
  TASK_CARD_BODY_MAX_PX,
  TASK_CARD_INNER_SCROLL_CLASS,
  TASK_CARD_NEXT_LANE_PEEK,
  TASK_CARD_SCROLLABLE_LIST_CLASS,
  TASK_CARD_WORKERS_CHROME,
  workStateLabel,
} from "./taskCardUtils";

describe("taskCardUtils", () => {
  it("maps execution status to work labels", () => {
    expect(workStateLabel("queued")).toBe("To Run");
    expect(workStateLabel("running")).toBe("Running");
    expect(workStateLabel("succeeded")).toBe("Done");
  });

  it("sorts executions with running first", () => {
    const sorted = sortExecutions([
      {
        id: "1",
        task_item_id: "item-1",
        event_id: "e1",
        event_title: "Done item",
        status: "succeeded",
        result_summary: null,
        error: null,
        conversation_id: null,
        attempt_no: 1,
        assigned_at: 1,
        started_at: null,
        finished_at: 3,
      },
      {
        id: "2",
        task_item_id: "item-2",
        event_id: "e2",
        event_title: "Running item",
        status: "running",
        result_summary: null,
        error: null,
        conversation_id: null,
        attempt_no: 1,
        assigned_at: 2,
        started_at: 2,
        finished_at: null,
      },
    ]);
    expect(sorted.map((row) => row.id)).toEqual(["2", "1"]);
  });

  it("keeps fifo order within the same status bucket", () => {
    const sorted = sortExecutions([
      {
        id: "3",
        task_item_id: "item-3",
        event_id: "e3",
        event_title: "Third queued",
        status: "queued",
        result_summary: null,
        error: null,
        conversation_id: null,
        attempt_no: 1,
        assigned_at: 30,
        started_at: null,
        finished_at: null,
      },
      {
        id: "1",
        task_item_id: "item-1",
        event_id: "e1",
        event_title: "First queued",
        status: "queued",
        result_summary: null,
        error: null,
        conversation_id: null,
        attempt_no: 1,
        assigned_at: 10,
        started_at: null,
        finished_at: null,
      },
      {
        id: "2",
        task_item_id: "item-2",
        event_id: "e2",
        event_title: "Second queued",
        status: "queued",
        result_summary: null,
        error: null,
        conversation_id: null,
        attempt_no: 1,
        assigned_at: 20,
        started_at: null,
        finished_at: null,
      },
    ]);
    expect(sorted.map((row) => row.id)).toEqual(["1", "2", "3"]);
  });

  it("sorts mixed statuses in fifo order within each bucket", () => {
    const base = {
      task_item_id: "item-base",
      result_summary: null,
      error: null,
      conversation_id: null,
      attempt_no: 1,
      started_at: null,
      finished_at: null,
    };
    const sorted = sortExecutions([
      {
        id: "d2",
        event_id: "e-d2",
        event_title: "Done second",
        status: "succeeded",
        assigned_at: 50,
        ...base,
        started_at: 50,
        finished_at: 55,
      },
      {
        id: "q2",
        event_id: "e-q2",
        event_title: "Queue second",
        status: "queued",
        assigned_at: 40,
        ...base,
      },
      {
        id: "r2",
        event_id: "e-r2",
        event_title: "Running second",
        status: "running",
        assigned_at: 20,
        ...base,
        started_at: 20,
      },
      {
        id: "d1",
        event_id: "e-d1",
        event_title: "Done first",
        status: "succeeded",
        assigned_at: 45,
        ...base,
        started_at: 45,
        finished_at: 48,
      },
      {
        id: "q1",
        event_id: "e-q1",
        event_title: "Queue first",
        status: "queued",
        assigned_at: 35,
        ...base,
      },
      {
        id: "r1",
        event_id: "e-r1",
        event_title: "Running first",
        status: "running",
        assigned_at: 15,
        ...base,
        started_at: 15,
      },
    ]);
    expect(sorted.map((row) => row.id)).toEqual(["r1", "r2", "q1", "q2", "d1", "d2"]);
  });

  it("returns only running executions when folded", () => {
    const folded = getFoldedExecutions([
      {
        id: "1",
        task_item_id: "item-1",
        event_id: "e1",
        event_title: "Done item",
        status: "succeeded",
        result_summary: null,
        error: null,
        conversation_id: null,
        attempt_no: 1,
        assigned_at: 1,
        started_at: null,
        finished_at: 3,
      },
      {
        id: "2",
        task_item_id: "item-2",
        event_id: "e2",
        event_title: "Running item",
        status: "running",
        result_summary: null,
        error: null,
        conversation_id: null,
        attempt_no: 1,
        assigned_at: 2,
        started_at: 2,
        finished_at: null,
      },
    ]);
    expect(folded.map((row) => row.id)).toEqual(["2"]);
  });

  it("finds execution by id across worker groups", () => {
    const workers = [
      {
        profile_id: "w1",
        executions: [
          {
            id: "exec-1",
            task_item_id: "item-1",
            event_id: "e1",
            event_title: "One",
            item: null,
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
    ];
    expect(findExecution(workers, "exec-1")?.id).toBe("exec-1");
    expect(findExecution(workers, null)).toBeNull();
    expect(findExecution(workers, "missing")).toBeNull();
  });

  it("marks queued executions as editable", () => {
    expect(isExecutionEditable("queued")).toBe(true);
    expect(isExecutionEditable("running")).toBe(false);
  });

  it("builds worker options from proposal and task history", () => {
    const options = buildWorkerOptions(
      ["worker-b"],
      { worker_profile_id: "worker-a", model: "composer-2.5" },
      "gpt-5",
    );
    expect(options).toEqual([
      { workerAgentId: "worker-a", model: "composer-2.5" },
      { workerAgentId: "worker-b", model: "gpt-5" },
    ]);
  });

  it("detects proposal edits", () => {
    const baseline = {
      worker_profile_id: "worker-a",
      title: "Title",
      description: "",
      instructions: "Do thing",
      model: "composer-2.5",
    };
    expect(
      proposalHasEdits(baseline, {
        workerAgentId: "worker-a",
        title: "Title",
        description: "",
        instructions: "Do thing",
        model: "composer-2.5",
      }),
    ).toBe(false);
    expect(
      proposalHasEdits(baseline, {
        workerAgentId: "worker-b",
        title: "Title",
        description: "",
        instructions: "Do thing",
        model: "composer-2.5",
      }),
    ).toBe(true);
  });

  it("exposes body max height for task card layout", () => {
    expect(taskCardBodyStyle()).toEqual({
      "--task-card-body-max": `${TASK_CARD_BODY_MAX_PX}px`,
      "--task-card-workers-chrome": TASK_CARD_WORKERS_CHROME,
      "--task-card-next-lane-peek": TASK_CARD_NEXT_LANE_PEEK,
    });
    expect(TASK_CARD_SCROLLABLE_LIST_CLASS).toContain("overflow-y-auto");
    expect(TASK_CARD_INNER_SCROLL_CLASS).toContain("overflow-y-auto");
    expect(TASK_CARD_INNER_SCROLL_CLASS).toContain("var(--task-card-body-max)");
  });
});
