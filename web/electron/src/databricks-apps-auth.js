// Browser sign-in for the Databricks Apps that OMNIGENT_DATABRICKS_APPS_BROWSER_SIGN_IN lists.
//
// A Databricks App signs its users in through its workspace. In the app window,
// that sign-in opens the workspace login page and then the SSO page. An SSO
// provider that requires a managed Chrome profile refuses that embedded page.
// For a listed app, the shell first signs in to the app's workspace in the
// system browser (databricks-session.js). The app's own sign-in then finds a
// workspace session and shows no login page. The workspace token and cookie
// never go to the app. An unlisted app keeps the in-window sign-in.
"use strict";

const https = require("node:https");

const { isDatabricksAppsServerUrl, isDatabricksOAuthServerUrl } = require("./url");
const { SESSION_REJECTED } = require("./databricks-auth");

// Comma-separated app URLs, e.g. "https://<app>.databricksapps.com".
const APPS_BROWSER_SIGN_IN_ENV = "OMNIGENT_DATABRICKS_APPS_BROWSER_SIGN_IN";
const DISCOVERY_TIMEOUT_MS = 10_000;
// A login page this soon after a new session means that the workspace rejected it.
const RECOVERY_RETRY_MS = 15_000;
// The workspace root exists on every workspace; the /omnigent mount does not.
const WORKSPACE_LANDING_PATH = "/";

function originOrNull(rawUrl, base) {
  try {
    return new URL(rawUrl, base).origin;
  } catch {
    return null;
  }
}

function browserSignInAppOrigins(env = process.env, isAppUrl = isDatabricksAppsServerUrl) {
  const origins = new Set();
  for (const entry of (env[APPS_BROWSER_SIGN_IN_ENV] ?? "").split(",")) {
    const value = entry.trim();
    if (isAppUrl(value)) origins.add(new URL(value).origin);
  }
  return origins;
}

function isWorkspaceLoginPage(rawUrl, workspaceOrigin) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return false;
  }
  const path = url.pathname.toLowerCase();
  return (
    url.origin === workspaceOrigin &&
    (path === "/login" || path.startsWith("/login/") || path === "/login.html")
  );
}

// Node's fetch always sends "sec-fetch-mode: cors", and the Apps sign-in gate answers
// that with 401 instead of its redirect, so the probe uses a plain HTTPS request.
function probeRedirect(url, { signal } = {}) {
  return new Promise((resolve, reject) => {
    const request = https.request(
      url,
      { method: "GET", headers: { accept: "text/html" }, signal, timeout: DISCOVERY_TIMEOUT_MS },
      (response) => {
        resolve({ status: response.statusCode ?? 0, location: response.headers.location ?? null });
        response.destroy();
      },
    );
    request.on("timeout", () => request.destroy(new Error("The app sign-in probe timed out")));
    request.on("error", reject);
    request.end();
  });
}

// The Apps sign-in gate answers a request without credentials with a redirect to its
// workspace. This request sends no cookies or tokens, and it accepts only a Databricks host.
// When the probe fails, the app keeps the in-window sign-in.
async function discoverWorkspaceOrigin(
  appOrigin,
  { probe = probeRedirect, signal, isWorkspaceOrigin = isDatabricksOAuthServerUrl } = {},
) {
  let answer;
  try {
    answer = await probe(`${appOrigin}/`, { signal });
  } catch {
    return null;
  }
  const { status, location } = answer;
  if (status < 300 || status >= 400 || !location) return null;
  const origin = originOrNull(location, appOrigin);
  return origin && isWorkspaceOrigin(origin) ? origin : null;
}

function sessionRejected() {
  return Object.assign(
    new Error("Databricks rejected the workspace session; sign in again in your browser"),
    { errorCode: SESSION_REJECTED },
  );
}

function createDatabricksAppsAuth({
  session,
  ensureSession,
  onAuthRequired,
  isAppUrl = isDatabricksAppsServerUrl,
  isWorkspaceOrigin = isDatabricksOAuthServerUrl,
  appOrigins = browserSignInAppOrigins(process.env, isAppUrl),
  probe = probeRedirect,
  now = Date.now,
}) {
  const workspaces = new Map();
  const connections = new Map();

  function covers(rawUrl) {
    return isAppUrl(rawUrl) && appOrigins.has(new URL(rawUrl).origin);
  }

  async function workspaceOriginFor(appOrigin, signal) {
    if (workspaces.has(appOrigin)) return workspaces.get(appOrigin);
    const origin = await discoverWorkspaceOrigin(appOrigin, { probe, signal, isWorkspaceOrigin });
    if (origin) workspaces.set(appOrigin, origin);
    return origin;
  }

  function current(ctx) {
    return connections.get(ctx.win) === ctx && !ctx.win.isDestroyed();
  }

  function detach(win) {
    const ctx = connections.get(win);
    if (!ctx) return;
    connections.delete(win);
    ctx.webContents.removeListener("will-redirect", ctx.onLeave);
    ctx.webContents.removeListener("will-navigate", ctx.onLeave);
    ctx.webContents.removeListener("did-navigate", ctx.onNavigate);
    ctx.webContents.removeListener("did-navigate-in-page", ctx.onNavigateInPage);
  }

  function fail(ctx, error) {
    if (!current(ctx)) return;
    detach(ctx.win);
    onAuthRequired(ctx.win, ctx.serverUrl, error, { returnUrl: ctx.returnUrl });
  }

  function recover(ctx) {
    if (ctx.pending) return ctx.pending;
    if (now() - ctx.signedInAt < RECOVERY_RETRY_MS) {
      fail(ctx, sessionRejected());
      return Promise.resolve();
    }
    ctx.pending = ensureSession(session, ctx.workspaceOrigin, {
      interactive: false,
      nextPath: WORKSPACE_LANDING_PATH,
    })
      .then(async () => {
        ctx.signedInAt = now();
        if (!current(ctx)) return;
        try {
          await ctx.win.loadURL(ctx.returnUrl);
        } catch (error) {
          console.warn("[omnigent] databricks apps auth: recovery reload did not land", {
            origin: ctx.appOrigin,
            code: error.code,
          });
        }
      })
      .catch((error) => fail(ctx, error))
      .finally(() => {
        ctx.pending = null;
      });
    return ctx.pending;
  }

  function attach(win, serverUrl, workspaceOrigin, loadUrl) {
    const appOrigin = new URL(serverUrl).origin;
    const ctx = {
      win,
      webContents: win.webContents,
      serverUrl,
      appOrigin,
      workspaceOrigin,
      returnUrl: loadUrl,
      pending: null,
      signedInAt: now(),
    };
    const remember = (url) => {
      if (originOrNull(url) === appOrigin) ctx.returnUrl = url;
    };
    ctx.onNavigate = (_event, url) => remember(url);
    ctx.onNavigateInPage = (_event, url, isMainFrame) => {
      if (isMainFrame) remember(url);
    };
    // Never show the workspace login page here: the SSO provider refuses the embedded page.
    // Renew the workspace session instead, and reload the app.
    ctx.onLeave = (event, url, _isInPlace, isMainFrame) => {
      if (isMainFrame === false || !isWorkspaceLoginPage(url, workspaceOrigin)) return;
      event.preventDefault();
      if (current(ctx)) void recover(ctx);
    };
    connections.set(win, ctx);
    win.webContents.on("will-redirect", ctx.onLeave);
    win.webContents.on("will-navigate", ctx.onLeave);
    win.webContents.on("did-navigate", ctx.onNavigate);
    win.webContents.on("did-navigate-in-page", ctx.onNavigateInPage);
  }

  async function prepare(
    win,
    serverUrl,
    { interactive = false, useStoredCredentials = true, loadUrl = serverUrl, signal } = {},
  ) {
    detach(win);
    const workspaceOrigin = await workspaceOriginFor(new URL(serverUrl).origin, signal);
    signal?.throwIfAborted();
    // Without a known workspace, the app loads with the in-window sign-in, as before.
    if (!workspaceOrigin) return null;
    await ensureSession(session, workspaceOrigin, {
      interactive,
      useStoredCredentials,
      nextPath: WORKSPACE_LANDING_PATH,
      signal,
    });
    signal?.throwIfAborted();
    if (!win.isDestroyed()) attach(win, serverUrl, workspaceOrigin, loadUrl);
    return workspaceOrigin;
  }

  return { covers, prepare, detach };
}

module.exports = {
  APPS_BROWSER_SIGN_IN_ENV,
  browserSignInAppOrigins,
  isWorkspaceLoginPage,
  discoverWorkspaceOrigin,
  createDatabricksAppsAuth,
};
