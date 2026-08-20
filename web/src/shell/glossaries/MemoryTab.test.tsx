import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryTab } from "./MemoryTab";

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  reorder: vi.fn(),
  settings: vi.fn(),
}));

const memory = {
  categories: [
    {
      id: "preferences",
      name: "Preferences",
      display_order: 0,
      content: "Keep answers concise.",
      token_count: 70,
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    },
    {
      id: "work",
      name: "Work",
      display_order: 1,
      content: "Managed Delta team.",
      token_count: 50,
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    },
  ],
  used_tokens: 120,
  max_tokens: 100,
  usage_percent: 120,
  over_limit: true,
};

vi.mock("@/hooks/useMemory", () => ({
  useMemory: () => ({ data: memory, isLoading: false, isError: false }),
  useCreateMemoryCategory: () => ({ mutateAsync: mocks.create, isPending: false }),
  useUpdateMemoryCategory: () => ({ mutateAsync: mocks.update, isPending: false }),
  useDeleteMemoryCategory: () => ({ mutateAsync: mocks.remove, isPending: false }),
  useReorderMemoryCategories: () => ({ mutateAsync: mocks.reorder, isPending: false }),
  useUpdateMemorySettings: () => ({ mutateAsync: mocks.settings, isPending: false }),
}));

describe("MemoryTab", () => {
  beforeEach(() => {
    mocks.create.mockReset();
    mocks.update.mockReset();
    mocks.remove.mockReset();
    mocks.reorder.mockReset();
    mocks.settings.mockReset();
    mocks.create.mockResolvedValue(memory);
    mocks.update.mockResolvedValue(memory);
    mocks.remove.mockResolvedValue({
      ...memory,
      categories: memory.categories.slice(1),
    });
    mocks.reorder.mockResolvedValue({
      ...memory,
      categories: [...memory.categories].reverse(),
    });
    mocks.settings.mockResolvedValue(memory);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("selects categories and autosaves edits to the category where they were made", async () => {
    vi.useFakeTimers();
    render(<MemoryTab />);
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: "Select Work" }));
    const workBoard = screen.getByRole("textbox", { name: "Work memory" });
    fireEvent.change(workBoard, { target: { value: "Updated work memory" } });
    expect(screen.getByText("Unsaved")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Select Preferences" }));
    expect(screen.getByRole("textbox", { name: "Preferences memory" })).toHaveValue(
      "Keep answers concise.",
    );

    await act(async () => {
      vi.advanceTimersByTime(1_200);
      await Promise.resolve();
    });
    expect(mocks.update).toHaveBeenCalledWith({
      id: "work",
      content: "Updated work memory",
    });
  });

  it("shows raw over-limit usage and truncation guidance", async () => {
    render(<MemoryTab />);
    expect(await screen.findByText("120% used")).toBeInTheDocument();
    expect(screen.getByText("120 tokens used")).toBeInTheDocument();
    expect(screen.getByText(/Overflow is truncated when memory is injected/)).toBeInTheDocument();
    expect(screen.getByText("OmniHarness only")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByTitle("Preferences: 70 injected tokens")).toBeInTheDocument();
    expect(screen.getByTitle("Work: 30 injected tokens")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Select Preferences" })).toHaveTextContent(
      "included:",
    );
    expect(screen.getByRole("button", { name: "Select Work" })).toHaveTextContent("partial:");
  });

  it("updates the memory token limit from the board", async () => {
    render(<MemoryTab />);
    const limit = await screen.findByRole("spinbutton", { name: "Memory token limit" });
    fireEvent.change(limit, { target: { value: "250" } });
    fireEvent.blur(limit);
    await waitFor(() => expect(mocks.settings).toHaveBeenCalledWith(250));
  });

  it("creates, reorders, and deletes categories", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<MemoryTab />);
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.change(screen.getByRole("textbox", { name: "New category name" }), {
      target: { value: "Projects" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[1]);
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith({ name: "Projects" }));

    fireEvent.click(screen.getByRole("button", { name: "Move Work up" }));
    await waitFor(() => expect(mocks.reorder).toHaveBeenCalledWith(["work", "preferences"]));

    fireEvent.click(screen.getByRole("button", { name: "Select Preferences" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Preferences" }));
    expect(confirm).toHaveBeenCalledWith('Delete non-empty category "Preferences"?');
    await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith("preferences"));
  });
});
