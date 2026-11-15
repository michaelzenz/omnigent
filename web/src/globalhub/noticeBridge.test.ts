import { describe, expect, it, vi } from "vitest";

import {
  connectNoticeFrame,
  injectNoticeBridge,
  NOTICE_BRIDGE_MSG,
  NOTICE_BRIDGE_SOURCE,
  parseNoticeBridgeMessage,
} from "./noticeBridge";

const NONCE = "nonce-1";

function bridgeMessage(fields: Record<string, unknown>): Record<string, unknown> {
  return { source: NOTICE_BRIDGE_SOURCE, nonce: NONCE, ...fields };
}

describe("parseNoticeBridgeMessage", () => {
  it("keeps an action's id and its true, false and text values", () => {
    const message = bridgeMessage({
      type: NOTICE_BRIDGE_MSG.action,
      action_id: "confirm",
      inputs: { delete_s1: true, delete_s2: false, note: "keep", count: 3 },
    });

    expect(parseNoticeBridgeMessage(message, NONCE)).toEqual({
      type: NOTICE_BRIDGE_MSG.action,
      action_id: "confirm",
      inputs: { delete_s1: true, delete_s2: false, note: "keep" },
    });
  });

  it.each([
    ["another nonce", { ...bridgeMessage({ type: "omni:action", action_id: "a" }), nonce: "x" }],
    ["another source", { ...bridgeMessage({ type: "omni:action", action_id: "a" }), source: "x" }],
    ["a blank action id", bridgeMessage({ type: "omni:action", action_id: " " })],
    [
      "inputs that are not an object",
      bridgeMessage({ type: "omni:action", action_id: "a", inputs: "x" }),
    ],
    ["an unknown type", bridgeMessage({ type: "omni:other" })],
    ["text", "omni:action"],
  ])("refuses %s", (_case, data) => {
    expect(parseNoticeBridgeMessage(data, NONCE)).toBeNull();
  });
});

describe("connectNoticeFrame", () => {
  it("hands the frame a private port and passes on only the declared actions", async () => {
    const postMessage = vi.fn();
    const onAction = vi.fn();

    const port = connectNoticeFrame(
      { postMessage } as unknown as Window,
      NONCE,
      (actionId) => actionId === "confirm",
      onAction,
    );
    const [init, origin, [framePort]] = postMessage.mock.calls[0] as [
      unknown,
      string,
      MessagePort[],
    ];
    framePort.postMessage(bridgeMessage({ type: NOTICE_BRIDGE_MSG.action, action_id: "other" }));
    framePort.postMessage(
      bridgeMessage({
        type: NOTICE_BRIDGE_MSG.action,
        action_id: "confirm",
        inputs: { delete_s1: true },
      }),
    );
    // A port delivers in order, so the undeclared action was handled first.
    await vi.waitFor(() => expect(onAction).toHaveBeenCalled());
    port.close();
    framePort.close();

    expect([init, origin, onAction.mock.calls]).toEqual([
      { source: NOTICE_BRIDGE_SOURCE, nonce: NONCE, type: NOTICE_BRIDGE_MSG.init },
      "*",
      [["confirm", { delete_s1: true }]],
    ]);
  });
});

describe("injectNoticeBridge", () => {
  it.each([
    [
      "before </body>",
      "<html><body><p>Hi</p></body></html>",
      /<p>Hi<\/p><script>.*<\/script><\/body>/s,
    ],
    ["before </html>", "<html><p>Hi</p></html>", /<p>Hi<\/p><script>.*<\/script><\/html>/s],
    ["at the end of a fragment", "<p>Hi</p>", /<p>Hi<\/p><script>.*<\/script>$/s],
  ])("puts the bridge script %s, with the frame's nonce", (_case, html, placement) => {
    const doc = injectNoticeBridge(html, NONCE);

    expect(doc).toMatch(placement);
    expect(doc).toContain(`var NONCE = "${NONCE}";`);
  });
});
