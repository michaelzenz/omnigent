#!/usr/bin/env node

const [serverUrl, portText] = process.argv.slice(2);
const debugPort = Number(portText);
const deadline = Date.now() + 20_000;

if (!serverUrl || !Number.isInteger(debugPort) || debugPort <= 0) {
  console.error("usage: check_electron_renderer.mjs <server-url> <debug-port>");
  process.exit(2);
}

if (typeof fetch !== "function" || typeof WebSocket !== "function") {
  console.error(
    "Node.js 22 or newer is required for the Electron renderer smoke check",
  );
  process.exit(2);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function findPage() {
  const expectedOrigin = new URL(serverUrl).origin;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${debugPort}/json/list`);
      if (response.ok) {
        const pages = await response.json();
        const page = pages.find((candidate) => {
          try {
            return (
              candidate.type === "page" &&
              new URL(candidate.url).origin === expectedOrigin
            );
          } catch {
            return false;
          }
        });
        if (page?.webSocketDebuggerUrl) return page;
      }
    } catch {
      // Electron may not have opened its debugging endpoint yet.
    }
    await sleep(100);
  }
  throw new Error(`Electron did not load ${serverUrl}`);
}

async function checkRenderer(page) {
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  const pending = new Map();
  const exceptions = [];
  let nextId = 0;

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const request = pending.get(message.id);
      clearTimeout(request.timer);
      request.resolve(message);
      pending.delete(message.id);
    }
    if (message.method === "Runtime.exceptionThrown") {
      exceptions.push(
        message.params.exceptionDetails.exception?.description ??
          message.params.exceptionDetails.text,
      );
    }
  });
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener(
      "error",
      () => reject(new Error("CDP websocket failed")),
      { once: true },
    );
  });

  socket.addEventListener("close", () => {
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(new Error("CDP websocket closed"));
    }
    pending.clear();
  });

  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++nextId;
      const remaining = Math.max(1, deadline - Date.now());
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`CDP ${method} timed out`));
      }, remaining);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });

  try {
    await send("Runtime.enable");
    await send("Page.enable");
    exceptions.length = 0;
    await send("Page.reload", { ignoreCache: true });

    while (Date.now() < deadline) {
      const response = await send("Runtime.evaluate", {
        expression:
          "({ ready: document.readyState, children: document.querySelector('#root')?.childElementCount ?? 0, text: document.body?.innerText?.trim().length ?? 0 })",
        returnByValue: true,
      });
      const state = response.result?.result?.value;
      if (exceptions.length > 0) throw new Error(exceptions.at(-1));
      if (state?.ready === "complete" && state.children > 0 && state.text > 0) {
        console.log(JSON.stringify(state));
        return;
      }
      await sleep(100);
    }
  } finally {
    socket.close();
  }

  const detail = exceptions.at(-1) ?? "React root stayed empty";
  throw new Error(detail);
}

try {
  await checkRenderer(await findPage());
} catch (error) {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exit(1);
}
