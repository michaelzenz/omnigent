"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { detectInstalledEditors, registerEditorApps } = require("../src/editorApps");

function appWithHandlers(handlers) {
  return { getApplicationNameForProtocol: (url) => handlers[url] ?? "" };
}

function register(app) {
  let handler;
  registerEditorApps({
    ipcMain: { handle: (_channel, callback) => (handler = callback) },
    app,
    isPinnedOriginSender: (event) => event.trusted === true,
  });
  return (event = { trusted: true }) => handler(event);
}

test("reports each editor that has an app for its links", () => {
  const ask = register(appWithHandlers({ "cursor://": "Cursor" }));
  assert.deepEqual(ask(), { vscode: false, cursor: true });
});

test("answers only the pinned server page", () => {
  const ask = register(appWithHandlers({ "cursor://": "Cursor", "vscode://": "Code" }));
  assert.equal(ask({ trusted: false }), null);
});

test("cannot tell without the Electron method or when it fails", () => {
  assert.equal(detectInstalledEditors({}), null);
  const failing = {
    getApplicationNameForProtocol: () => {
      throw new Error("no handler registry");
    },
  };
  assert.equal(detectInstalledEditors(failing), null);
});
