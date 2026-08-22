import { afterEach, describe, expect, it } from "vitest";

import {
  isSendMessageShortcut,
  readSendMessageShortcut,
  writeSendMessageShortcut,
} from "./sendMessagePreferences";

afterEach(() => {
  localStorage.clear();
});

describe("send message shortcut preference", () => {
  it("defaults to Enter", () => {
    expect(readSendMessageShortcut()).toBe("enter");
    expect(
      isSendMessageShortcut({ key: "Enter", shiftKey: false, metaKey: false, ctrlKey: false }),
    ).toBe(true);
  });

  it("requires Command or Ctrl when configured", () => {
    writeSendMessageShortcut("command-enter");

    expect(
      isSendMessageShortcut({ key: "Enter", shiftKey: false, metaKey: false, ctrlKey: false }),
    ).toBe(false);
    expect(
      isSendMessageShortcut({ key: "Enter", shiftKey: false, metaKey: true, ctrlKey: false }),
    ).toBe(true);
    expect(
      isSendMessageShortcut({ key: "Enter", shiftKey: false, metaKey: false, ctrlKey: true }),
    ).toBe(true);
    expect(
      isSendMessageShortcut({ key: "Enter", shiftKey: true, metaKey: true, ctrlKey: false }),
    ).toBe(false);
  });
});
