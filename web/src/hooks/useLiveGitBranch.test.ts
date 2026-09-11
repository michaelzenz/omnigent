import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { getSessionGitBranch } from "@/lib/sessionsApi";
import { setterFor } from "@/store/chatStore";
import { useLiveGitBranch } from "./useLiveGitBranch";

vi.mock("@/lib/sessionsApi", () => ({
  getSessionGitBranch: vi.fn(),
}));

vi.mock("@/store/chatStore", () => ({
  setterFor: vi.fn(() => vi.fn()),
}));

const mockedGet = vi.mocked(getSessionGitBranch);
const mockedSetterFor = vi.mocked(setterFor);

function currentSetter(): ReturnType<typeof vi.fn> {
  const s = mockedSetterFor.mock.results.at(-1)?.value;
  return (s ?? vi.fn()) as ReturnType<typeof vi.fn>;
}

describe("useLiveGitBranch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom's visibilityState is already "visible" — stub only the property
    // so other visibility-driven branches can flip it without clobbering
    // the whole document object (render needs the real one).
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches the live branch and writes it via the named-conversation setter", async () => {
    mockedGet.mockResolvedValue("switched-branch");

    const { unmount } = renderHook(() => useLiveGitBranch("conv_1"));
    await vi.waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith("conv_1");
      expect(mockedSetterFor).toHaveBeenCalledWith("conv_1");
      expect(currentSetter()).toHaveBeenCalledWith({ gitBranch: "switched-branch" });
    });
    unmount();
  });

  it("ignores a null branch (non-git workspace)", async () => {
    mockedGet.mockResolvedValue(null);

    const { unmount } = renderHook(() => useLiveGitBranch("conv_1"));
    await vi.waitFor(() => expect(mockedGet).toHaveBeenCalled());
    expect(mockedSetterFor).not.toHaveBeenCalled();
    unmount();
  });

  it("keeps the last branch on fetch failure", async () => {
    mockedGet.mockRejectedValue(new Error("offline"));

    const { unmount } = renderHook(() => useLiveGitBranch("conv_1"));
    await vi.waitFor(() => expect(mockedGet).toHaveBeenCalled());
    expect(mockedSetterFor).not.toHaveBeenCalled();
    unmount();
  });

  it("does nothing without a conversation id", async () => {
    mockedGet.mockResolvedValue("x");

    const { unmount } = renderHook(() => useLiveGitBranch(null));
    await Promise.resolve();
    expect(mockedGet).not.toHaveBeenCalled();
    unmount();
  });
});
