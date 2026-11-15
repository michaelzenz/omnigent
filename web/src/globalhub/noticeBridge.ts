// Bridge between the board and an HTML system notice.
//
// The custodian writes the notice's HTML, so the card runs it in a sandboxed
// frame without `allow-same-origin` (HTML_PREVIEW_SANDBOX): its scripts run in
// an opaque origin and cannot reach the app. Clicks flow one way out of the
// frame: an element with `data-action="<action id>"` sends the action, with the
// values of the visible, enabled `data-notice-input="<input id>"` controls, to
// the card, which sends the click to the server. The frame never talks to the
// server itself.
//
// Trust: after the handshake, messages travel over a port that only the card
// and the injected script hold. The handshake reaches every listener in the
// frame, so the notice's own scripts could take the port and send clicks; the
// card sends only the notice's declared actions, and the server checks them
// again. A per-frame nonce and a source tag reject other traffic.

import { prepareHtmlPreviewDoc } from "@/shell/codeViewerHelpers";

/** Tag on every bridge message, so other postMessage traffic is ignored. */
export const NOTICE_BRIDGE_SOURCE = "omni-system-notice";

export const NOTICE_BRIDGE_MSG = {
  /** card → frame: hands over the port (transferred). */
  init: "omni:init",
  /** frame → card: the port is adopted. */
  ready: "omni:ready",
  /** frame → card: the user activated a data-action element. */
  action: "omni:action",
} as const;

/** A click the frame relays: the action and the values of its visible controls. */
export interface NoticeBridgeAction {
  type: typeof NOTICE_BRIDGE_MSG.action;
  action_id: string;
  inputs: Record<string, boolean | string>;
}

/** A message the frame can send after the handshake. */
export type NoticeBridgeMessage = NoticeBridgeAction | { type: typeof NOTICE_BRIDGE_MSG.ready };

/**
 * Check a message from the notice frame.
 *
 * @returns The message, or ``null`` when it is malformed or carries another nonce.
 */
export function parseNoticeBridgeMessage(data: unknown, nonce: string): NoticeBridgeMessage | null {
  if (typeof data !== "object" || data === null) return null;
  const message = data as Record<string, unknown>;
  if (message.source !== NOTICE_BRIDGE_SOURCE || message.nonce !== nonce) return null;
  if (message.type === NOTICE_BRIDGE_MSG.ready) return { type: NOTICE_BRIDGE_MSG.ready };
  if (message.type !== NOTICE_BRIDGE_MSG.action) return null;
  if (typeof message.action_id !== "string" || message.action_id.trim() === "") return null;
  const inputs: Record<string, boolean | string> = {};
  if (message.inputs !== undefined) {
    if (typeof message.inputs !== "object" || message.inputs === null) return null;
    for (const [inputId, value] of Object.entries(message.inputs)) {
      if (typeof value === "boolean" || typeof value === "string") inputs[inputId] = value;
    }
  }
  return { type: NOTICE_BRIDGE_MSG.action, action_id: message.action_id, inputs };
}

/**
 * Hand a loaded notice frame a private port, and pass on the clicks of its declared actions.
 *
 * @param frame - The frame's window.
 * @param nonce - The nonce in the frame's injected script.
 * @param isDeclared - Whether the notice declares an action id.
 * @param onAction - Called with each declared action the frame relays.
 * @returns The card's end of the channel; close it to stop listening.
 */
export function connectNoticeFrame(
  frame: Window,
  nonce: string,
  isDeclared: (actionId: string) => boolean,
  onAction: (actionId: string, inputs: Record<string, boolean | string>) => void,
): MessagePort {
  const channel = new MessageChannel();
  channel.port1.onmessage = (event: MessageEvent) => {
    const message = parseNoticeBridgeMessage(event.data, nonce);
    if (message?.type === NOTICE_BRIDGE_MSG.action && isDeclared(message.action_id)) {
      onAction(message.action_id, message.inputs);
    }
  };
  // "*" because the sandboxed frame's origin is opaque; the port and the nonce bind the bridge.
  frame.postMessage({ source: NOTICE_BRIDGE_SOURCE, nonce, type: NOTICE_BRIDGE_MSG.init }, "*", [
    channel.port2,
  ]);
  return channel.port1;
}

// The script injected into the frame: it adopts the card's port, then relays
// data-action clicks with the values of the visible, enabled data-notice-input
// controls. buildNoticeBridgeScript fills in the placeholders.
const NOTICE_BRIDGE_SCRIPT_BODY = `
(function () {
  "use strict";
  var NONCE = "__OMNI_NONCE__";
  var SRC = "__OMNI_SRC__";
  var T = __OMNI_TYPES__;
  var port = null;

  function send(msg) {
    if (!port) return;
    try { port.postMessage(msg); } catch (e) {}
  }

  function isCollectable(el) {
    // The user cannot have seen or set a hidden or disabled control.
    if (el.disabled) return false;
    var rects = el.getClientRects ? el.getClientRects() : [];
    if (!rects || rects.length === 0) return false;
    var style = window.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    var rect = el.getBoundingClientRect();
    if (rect.width <= 0 && rect.height <= 0) return false;
    return true;
  }

  function collectInputs() {
    var inputs = {};
    var nodes = document.querySelectorAll("[data-notice-input]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (!isCollectable(el)) continue;
      var inputId = el.getAttribute("data-notice-input");
      if (!inputId) continue;
      var tag = (el.tagName || "").toLowerCase();
      if (tag === "input") {
        var type = (el.getAttribute("type") || "text").toLowerCase();
        if (type === "checkbox" || type === "radio") {
          if (el.checked) inputs[inputId] = true;
        } else if (type !== "hidden") {
          inputs[inputId] = el.value;
        }
      } else if (tag === "textarea" || tag === "select") {
        inputs[inputId] = el.value;
      }
    }
    return inputs;
  }

  window.addEventListener("message", function (ev) {
    var data = ev.data;
    if (!data || data.source !== SRC || data.nonce !== NONCE) return;
    if (data.type === T.init && ev.ports && ev.ports.length > 0) {
      port = ev.ports[0];
      port.start();
      port.onmessage = function () {};
      send({ source: SRC, nonce: NONCE, type: T.ready });
    }
  });

  document.addEventListener(
    "click",
    function (ev) {
      if (!port) return;
      var target = ev.target;
      if (!target || typeof target.closest !== "function") return;
      var actionEl = target.closest("[data-action]");
      if (!actionEl || !actionEl.getAttribute("data-action")) return;
      var actionId = actionEl.getAttribute("data-action");
      var inputs = collectInputs();
      if (ev.cancelable) ev.preventDefault();
      send({ source: SRC, nonce: NONCE, type: T.action, action_id: actionId, inputs: inputs });
    },
    true
  );
})();`;

/** The injected bridge script with ``nonce`` filled in. */
export function buildNoticeBridgeScript(nonce: string): string {
  return NOTICE_BRIDGE_SCRIPT_BODY.replace("__OMNI_NONCE__", nonce)
    .replace("__OMNI_SRC__", NOTICE_BRIDGE_SOURCE)
    .replace("__OMNI_TYPES__", JSON.stringify(NOTICE_BRIDGE_MSG));
}

/**
 * Prepare notice HTML for the card's frame: links open in a new tab (main's
 * preview rule), and the bridge script runs once the body is parsed. Like
 * main's comment bridge, this places the script with a simple search, not an
 * HTML parse: before ``</body>``, else before ``</html>``, else at the end.
 */
export function injectNoticeBridge(html: string, nonce: string): string {
  const prepared = prepareHtmlPreviewDoc(html);
  const script = `<script>${buildNoticeBridgeScript(nonce)}</script>`;
  const bodyClose = prepared.search(/<\/body\s*>/i);
  if (bodyClose !== -1) return prepared.slice(0, bodyClose) + script + prepared.slice(bodyClose);
  const htmlClose = prepared.search(/<\/html\s*>/i);
  if (htmlClose !== -1) return prepared.slice(0, htmlClose) + script + prepared.slice(htmlClose);
  return prepared + script;
}
