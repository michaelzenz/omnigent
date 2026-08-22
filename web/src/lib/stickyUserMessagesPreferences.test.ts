import { afterEach, describe, expect, it, vi } from "vitest";
import {
  readStickyUserMessagesEnabled,
  subscribeStickyUserMessagesEnabled,
  writeStickyUserMessagesEnabled,
} from "./stickyUserMessagesPreferences";

afterEach(() => {
  localStorage.clear();
});

describe("sticky user message preferences", () => {
  it("defaults to enabled and stores only the non-default choice", () => {
    expect(readStickyUserMessagesEnabled()).toBe(true);

    writeStickyUserMessagesEnabled(false);
    expect(readStickyUserMessagesEnabled()).toBe(false);
    expect(localStorage.getItem("omnigent:sticky-user-messages")).toBe("false");

    writeStickyUserMessagesEnabled(true);
    expect(localStorage.getItem("omnigent:sticky-user-messages")).toBeNull();
  });

  it("notifies mounted chat views when the setting changes", () => {
    const onChange = vi.fn();
    const unsubscribe = subscribeStickyUserMessagesEnabled(onChange);

    writeStickyUserMessagesEnabled(false);
    expect(onChange).toHaveBeenCalledOnce();

    unsubscribe();
    writeStickyUserMessagesEnabled(true);
    expect(onChange).toHaveBeenCalledOnce();
  });
});
