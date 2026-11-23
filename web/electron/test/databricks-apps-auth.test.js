"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const {
  APPS_BROWSER_SIGN_IN_ENV,
  browserSignInAppOrigins,
  isWorkspaceLoginPage,
  discoverWorkspaceOrigin,
  createDatabricksAppsAuth,
} = require("../src/databricks-apps-auth");
const { SESSION_REJECTED } = require("../src/databricks-auth");

const APP = "https://my-app.apps.example";
const OTHER_APP = "https://other-app.apps.example";
const WORKSPACE = "https://workspace.example";
const AUTHORIZE = `${WORKSPACE}/oidc/oauth2/v2.0/authorize?client_id=app`;
const LOGIN = `${WORKSPACE}/login.html?next_url=%2Foidc%2Foauth2%2Fv2.0%2Fauthorize`;
const NOW = 1_800_000_000_000;

function isAppUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === "https:" && url.hostname.endsWith(".apps.example");
  } catch {
    return false;
  }
}

const isWorkspaceOrigin = (origin) => origin === WORKSPACE;

const drain = () =>
  new Promise((resolve) => {
    setImmediate(resolve);
  });

function redirectTo(location, status = 302) {
  return { status, headers: new Headers(location ? { location } : {}), body: null };
}

function harness({ response = redirectTo(AUTHORIZE), ensureSession } = {}) {
  let clock = NOW;
  const calls = { fetch: [], ensure: [], load: [], authRequired: [] };
  const session = { name: "default" };
  const auth = createDatabricksAppsAuth({
    session,
    isAppUrl,
    isWorkspaceOrigin,
    appOrigins: new Set([APP]),
    fetchFn: async (url, init) => {
      calls.fetch.push({ url, init });
      return response;
    },
    ensureSession: async (...args) => {
      calls.ensure.push(args);
      if (ensureSession) return ensureSession(...args);
      return args[1];
    },
    onAuthRequired: (win, url, error, options) =>
      calls.authRequired.push({ win, url, error, options }),
    now: () => clock,
  });
  function window() {
    return {
      webContents: new EventEmitter(),
      isDestroyed: () => false,
      loadURL: async (url) => {
        calls.load.push(url);
      },
    };
  }
  function redirect(win, url) {
    let prevented = false;
    win.webContents.emit(
      "will-redirect",
      { preventDefault: () => (prevented = true) },
      url,
      false,
      true,
    );
    return prevented;
  }
  return { auth, calls, session, window, redirect, advance: (ms) => (clock += ms) };
}

describe("browserSignInAppOrigins", () => {
  it("keeps the https app origins that the variable lists", () => {
    const origins = browserSignInAppOrigins(
      {
        [APPS_BROWSER_SIGN_IN_ENV]: ` ${APP}/c/chat , ${OTHER_APP},${WORKSPACE},http://my-app.apps.example,,not a url`,
      },
      isAppUrl,
    );

    assert.deepEqual([...origins], [APP, OTHER_APP]);
    assert.equal(browserSignInAppOrigins({}, isAppUrl).size, 0);
  });
});

describe("isWorkspaceLoginPage", () => {
  it("matches only the login pages of the app's workspace", () => {
    assert.equal(isWorkspaceLoginPage(LOGIN, WORKSPACE), true);
    assert.equal(isWorkspaceLoginPage(`${WORKSPACE}/login/sso`, WORKSPACE), true);
    assert.equal(isWorkspaceLoginPage(AUTHORIZE, WORKSPACE), false);
    assert.equal(isWorkspaceLoginPage(`${APP}/login.html`, WORKSPACE), false);
    assert.equal(isWorkspaceLoginPage("not a url", WORKSPACE), false);
  });
});

describe("discoverWorkspaceOrigin", () => {
  it("reads the workspace from the app's sign-in redirect without following it", async () => {
    const requests = [];
    const fetchFn = async (url, init) => {
      requests.push({ url, redirect: init.redirect });
      return redirectTo(AUTHORIZE);
    };

    assert.equal(await discoverWorkspaceOrigin(APP, { fetchFn, isWorkspaceOrigin }), WORKSPACE);
    assert.deepEqual(requests, [{ url: `${APP}/`, redirect: "manual" }]);
  });

  it("finds no workspace for a foreign redirect or an answer without one", async () => {
    const responses = [
      redirectTo("https://example.com/authorize"),
      redirectTo(null),
      { status: 403, headers: new Headers(), body: null },
    ];

    const found = await Promise.all(
      responses.map((response) =>
        discoverWorkspaceOrigin(APP, { fetchFn: async () => response, isWorkspaceOrigin }),
      ),
    );

    assert.deepEqual(found, [null, null, null]);
  });
});

describe("createDatabricksAppsAuth", () => {
  it("covers only the listed apps", () => {
    const { auth } = harness();

    assert.equal(auth.covers(`${APP}/c/chat`), true);
    assert.equal(auth.covers(OTHER_APP), false);
    assert.equal(auth.covers(WORKSPACE), false);
    assert.equal(auth.covers("not a url"), false);
  });

  it("signs in to the app's workspace before the load, and finds the workspace once", async () => {
    const { auth, calls, session, window } = harness();
    const controller = new AbortController();

    const first = await auth.prepare(window(), APP, {
      interactive: true,
      useStoredCredentials: false,
      signal: controller.signal,
    });
    await auth.prepare(window(), APP);

    assert.equal(first, WORKSPACE);
    assert.equal(calls.fetch.length, 1);
    assert.deepEqual(calls.ensure[0], [
      session,
      WORKSPACE,
      { interactive: true, useStoredCredentials: false, nextPath: "/", signal: controller.signal },
    ]);
    assert.equal(calls.ensure[1][2].interactive, false);
  });

  it("loads the app as before when its workspace is unknown", async () => {
    const { auth, calls, redirect, window } = harness({ response: redirectTo(null) });
    const win = window();

    assert.equal(await auth.prepare(win, APP, { interactive: true }), null);
    assert.equal(calls.ensure.length, 0);
    assert.equal(redirect(win, LOGIN), false);
  });

  it("renews the workspace session when the app sends the window to the login page", async () => {
    const { auth, calls, redirect, window, advance } = harness();
    const win = window();
    await auth.prepare(win, APP, { interactive: true, loadUrl: `${APP}/` });
    win.webContents.emit("did-navigate", {}, `${APP}/c/chat`);
    advance(60_000);

    const authorizePrevented = redirect(win, AUTHORIZE);
    const loginPrevented = redirect(win, LOGIN);
    await drain();

    assert.deepEqual([authorizePrevented, loginPrevented], [false, true]);
    assert.deepEqual(calls.ensure[1].slice(1), [WORKSPACE, { interactive: false, nextPath: "/" }]);
    assert.deepEqual(calls.load, [`${APP}/c/chat`]);
    assert.equal(calls.authRequired.length, 0);
  });

  it("asks for a browser sign-in when the workspace rejects a session it just made", async () => {
    const { auth, calls, redirect, window } = harness();
    const win = window();
    await auth.prepare(win, APP, { interactive: true });

    assert.equal(redirect(win, LOGIN), true);
    await drain();

    assert.equal(calls.ensure.length, 1);
    assert.equal(calls.authRequired.length, 1);
    assert.equal(calls.authRequired[0].url, APP);
    assert.equal(calls.authRequired[0].error.errorCode, SESSION_REJECTED);
    assert.equal(redirect(win, LOGIN), false);
  });

  it("asks for a sign-in when the silent renewal fails", async () => {
    const renewal = new Error("Session expired");
    let call = 0;
    const { auth, calls, redirect, window, advance } = harness({
      ensureSession: async (_session, origin) => {
        call += 1;
        if (call > 1) throw renewal;
        return origin;
      },
    });
    const win = window();
    await auth.prepare(win, APP);
    advance(60_000);

    redirect(win, LOGIN);
    await drain();

    assert.deepEqual(
      calls.authRequired.map(({ url, error }) => ({ url, error })),
      [{ url: APP, error: renewal }],
    );
    assert.equal(calls.load.length, 0);
  });

  it("stops watching a window once it is detached", async () => {
    const { auth, redirect, window } = harness();
    const win = window();
    await auth.prepare(win, APP);

    auth.detach(win);

    assert.equal(redirect(win, LOGIN), false);
    assert.equal(win.webContents.listenerCount("will-redirect"), 0);
  });
});
