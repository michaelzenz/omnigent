import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MemoryHost, MemorySyncState } from "@/hooks/useMemorySync";
import { MemoryTab } from "./MemoryTab";

const { writeMemory, refetch, memory } = vi.hoisted(() => ({
  writeMemory: vi.fn(),
  refetch: vi.fn(),
  memory: { data: null as MemorySyncState | null },
}));

vi.mock("@/hooks/useMemorySync", () => ({
  useMemorySync: () => ({
    data: memory.data,
    isPending: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch,
  }),
  useWriteMemory: () => ({ mutateAsync: writeMemory, isPending: false }),
}));

function host(
  hostId: string,
  hostName: string,
  state: MemoryHost["state"],
  contentSha256: string | null = null,
): MemoryHost {
  return { hostId, hostName, online: true, state, contentSha256, error: null };
}

const TWO_VARIANTS: MemorySyncState = {
  file: "~/AGENTS.md",
  variants: [
    { contentSha256: "aaa", content: "shared rules\n", hostIds: ["laptop", "desktop"] },
    { contentSha256: "bbb", content: "remote rules\n", hostIds: ["remote"] },
  ],
  hosts: [
    host("laptop", "Laptop", "present", "aaa"),
    host("desktop", "Desktop", "present", "aaa"),
    host("remote", "Remote", "present", "bbb"),
    host("empty", "Empty", "missing"),
    host("old", "Old", "needs_update"),
  ],
};

function result(hostId: string, status: "done" | "conflict", contentSha256: string | null) {
  return { hostId, status, contentSha256, error: null };
}

function editTo(text: string) {
  fireEvent.change(screen.getByTestId("memory-editor"), { target: { value: text } });
}

beforeEach(() => {
  memory.data = TWO_VARIANTS;
  writeMemory.mockResolvedValue([]);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("MemoryTab", () => {
  it("shows each host's variant and saves an edit to the hosts of its variant", async () => {
    writeMemory.mockImplementation(async () => {
      memory.data = {
        ...TWO_VARIANTS,
        variants: [
          { contentSha256: "ccc", content: "shared rules\nmore\n", hostIds: ["laptop", "desktop"] },
          TWO_VARIANTS.variants[1],
        ],
      };
      return [result("laptop", "done", "ccc"), result("desktop", "done", "ccc")];
    });
    render(<MemoryTab />);

    expect(screen.getByText("2 variants")).toBeInTheDocument();
    expect(screen.getByTestId("memory-host-remote")).toHaveTextContent("RemoteVariant 2");
    expect(screen.getByTestId("memory-host-empty")).toHaveTextContent("No file");
    expect(screen.getByTestId("memory-host-old")).toHaveTextContent("Update needed");
    expect(screen.getByTestId("memory-save")).toBeDisabled();

    editTo("shared rules\nmore\n");
    fireEvent.click(screen.getByTestId("memory-save"));

    await waitFor(() =>
      expect(writeMemory).toHaveBeenCalledWith({
        content: "shared rules\nmore\n",
        targets: [
          { hostId: "laptop", expectedSha256: "aaa" },
          { hostId: "desktop", expectedSha256: "aaa" },
        ],
      }),
    );
    expect(await screen.findByText("Saved to 2 hosts.")).toBeInTheDocument();
    expect(screen.getByTestId("memory-editor")).toHaveValue("shared rules\nmore\n");
    expect(screen.getByTestId("memory-save")).toBeDisabled();
  });

  it("keeps the edited text when every host of its variant changed in the meantime", async () => {
    writeMemory.mockImplementation(async () => {
      memory.data = {
        ...TWO_VARIANTS,
        variants: [
          { contentSha256: "ddd", content: "agent edit\n", hostIds: ["laptop", "desktop"] },
          TWO_VARIANTS.variants[1],
        ],
      };
      return [result("laptop", "conflict", null), result("desktop", "conflict", null)];
    });
    render(<MemoryTab />);

    editTo("my edit\n");
    fireEvent.click(screen.getByTestId("memory-save"));

    expect(
      await screen.findByText(/Laptop: its file changed after you loaded it.*discard it\.$/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("memory-editor")).toHaveValue("my edit\n");
    expect(screen.getByTestId("memory-save")).toBeDisabled();
  });

  it("asks before Sync to all hosts replaces another variant", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    writeMemory.mockResolvedValue(
      ["laptop", "desktop", "remote", "empty"].map((hostId) => result(hostId, "done", "aaa")),
    );
    render(<MemoryTab />);

    fireEvent.click(screen.getByTestId("memory-sync-all"));

    expect(confirm).toHaveBeenCalledWith("Replace ~/AGENTS.md on Remote with this text?");
    await waitFor(() =>
      expect(writeMemory).toHaveBeenCalledWith({
        content: "shared rules\n",
        targets: [
          { hostId: "laptop", expectedSha256: "aaa" },
          { hostId: "desktop", expectedSha256: "aaa" },
          { hostId: "remote", expectedSha256: "bbb" },
          { hostId: "empty", expectedSha256: null },
        ],
      }),
    );
    expect(
      await screen.findByText("Synced to 4 hosts. Skipped Old (Update needed)."),
    ).toBeInTheDocument();
  });
});
