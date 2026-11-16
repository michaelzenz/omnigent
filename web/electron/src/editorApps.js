"use strict";

/**
 * Which code editors this OS opens `vscode://` and `cursor://` links with, or
 * null when Electron cannot tell.
 */
function detectInstalledEditors(app) {
  if (typeof app.getApplicationNameForProtocol !== "function") return null;
  try {
    return {
      vscode: app.getApplicationNameForProtocol("vscode://") !== "",
      cursor: app.getApplicationNameForProtocol("cursor://") !== "",
    };
  } catch {
    return null;
  }
}

/** Answer the pinned server page, so its Open menu lists only editors that can open. */
function registerEditorApps({ ipcMain, app, isPinnedOriginSender }) {
  ipcMain.handle("omnigent:installed-editors", (event) =>
    isPinnedOriginSender(event) ? detectInstalledEditors(app) : null,
  );
}

module.exports = { detectInstalledEditors, registerEditorApps };
