import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useHiddenSkills } from "./useHiddenSkills";

const identity = vi.hoisted(() => ({
  user: "alice" as string | null,
  listeners: new Set<() => void>(),
}));

vi.mock("@/lib/globalHubStorage", () => ({
  globalHubStorageKey: (name: string) =>
    identity.user === null ? null : `test.${name}:${identity.user}`,
}));
vi.mock("@/lib/identity", () => ({
  subscribeIdentity: (listener: () => void) => {
    identity.listeners.add(listener);
    return () => identity.listeners.delete(listener);
  },
}));

function hiddenFor(user: string | null): string[] {
  identity.user = user;
  const { result, unmount } = renderHook(() => useHiddenSkills());
  const hidden = result.current[0];
  unmount();
  return hidden;
}

describe("useHiddenSkills", () => {
  beforeEach(() => {
    localStorage.clear();
    identity.user = "alice";
    identity.listeners.clear();
  });

  it("keeps each signed-in user's hidden skills apart", () => {
    const alice = renderHook(() => useHiddenSkills());
    act(() => alice.result.current[1]("pdf"));
    act(() => alice.result.current[1]("notes"));
    act(() => alice.result.current[1]("notes"));
    alice.unmount();

    expect(hiddenFor("bob")).toEqual([]);
    expect(hiddenFor("alice")).toEqual(["pdf"]);
  });

  it("keeps changes in memory until the user is known, then loads the user's list", () => {
    localStorage.setItem("test.hidden-skills:alice", JSON.stringify(["saved"]));
    identity.user = null;
    const { result } = renderHook(() => useHiddenSkills());

    act(() => result.current[1]("pdf"));
    const beforeSignIn = result.current[0];
    act(() => {
      identity.user = "alice";
      identity.listeners.forEach((listener) => listener());
    });

    expect(beforeSignIn).toEqual(["pdf"]);
    expect(localStorage.getItem("test.hidden-skills:alice")).toBe(JSON.stringify(["saved"]));
    expect(result.current[0]).toEqual(["saved"]);
  });
});
