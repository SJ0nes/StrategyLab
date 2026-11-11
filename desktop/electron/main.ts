/**
 * The Electron main process: starts the local StrategyLab server, opens one
 * window on it, and stops the server when the window goes.
 *
 * It holds no trading logic and reads no project file. Everything the window
 * shows comes from the server, which calls the same functions the commands do.
 *
 * THE TOKEN NEVER LEAVES THIS PROCESS EXCEPT IN A REQUEST HEADER. It is made
 * here, handed to the server in its environment, and added here to each request
 * the window sends to the server's API. The page's own script never holds it,
 * and it is on no command line, where any process on the machine could read it.
 */
import { type ChildProcess, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync } from "node:fs";
import path from "node:path";
import readline from "node:readline";

import { BrowserWindow, app, shell } from "electron";

import { findOnPath } from "./findOnPath";

/** The repository root: this file runs from `desktop/dist-electron/`. */
const REPOSITORY = path.resolve(__dirname, "..", "..");

/** The environment variable the server reads its token from. */
const TOKEN_VARIABLE = "STRATEGYLAB_APP_TOKEN";

/** How long the server may take to say it is ready, in milliseconds. */
const READY_TIMEOUT_MS = 60_000;

let server: ChildProcess | null = null;

/**
 * Start `strategylab serve` and resolve with the port it chose.
 *
 * The server's standard input is kept open as a lifeline: the server exits
 * when it closes, so a window that crashes does not leave a server behind.
 * `--frozen` runs the project exactly as its lockfile says, without resolving
 * dependencies again at launch.
 */
function startServer(launchToken: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const uv = findOnPath("uv", process.env, process.platform, existsSync);
    if (uv === null) {
      reject(new Error("`uv` was not found on PATH; StrategyLab's server is started with it"));
      return;
    }
    const child = spawn(uv, ["run", "--frozen", "strategylab", "serve", "--port", "0"], {
      cwd: REPOSITORY,
      env: { ...process.env, [TOKEN_VARIABLE]: launchToken },
      stdio: ["pipe", "pipe", "inherit"],
      windowsHide: true,
    });
    server = child;

    const timer = setTimeout(
      () => reject(new Error("the StrategyLab server did not report a port in time")),
      READY_TIMEOUT_MS,
    );
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`the StrategyLab server stopped before it was ready (exit ${code})`));
    });

    const lines = readline.createInterface({ input: child.stdout! });
    lines.on("line", (line) => {
      try {
        const message: unknown = JSON.parse(line);
        if (typeof message === "object" && message !== null && "port" in message) {
          const port = (message as { port: unknown }).port;
          if (typeof port === "number") {
            clearTimeout(timer);
            resolve(port);
          }
        }
      } catch {
        // Not the ready line. The server may print other things; ignore them.
      }
    });
  });
}

/** Close the server's lifeline, which is its signal to exit. */
function stopServer(): void {
  if (server !== null) {
    server.stdin?.end();
    server = null;
  }
}

async function openWindow(): Promise<void> {
  const launchToken = randomBytes(32).toString("hex");
  const port = await startServer(launchToken);
  const origin = `http://127.0.0.1:${port}`;

  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    title: "StrategyLab",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  window.removeMenu();

  // The token is added to API requests here, on their way out. A WebSocket to
  // the API gets it the same way.
  const api = [`${origin}/api/*`, `ws://127.0.0.1:${port}/api/*`];
  window.webContents.session.webRequest.onBeforeSendHeaders({ urls: api }, (details, callback) => {
    callback({ requestHeaders: { ...details.requestHeaders, Authorization: `Bearer ${launchToken}` } });
  });

  // The window shows this server and nothing else. A link to anywhere else
  // opens in the person's own browser, and only if it is an https link.
  const stayHere = (event: Electron.Event, url: string): void => {
    if (!url.startsWith(`${origin}/`)) {
      event.preventDefault();
    }
  };
  window.webContents.on("will-navigate", stayHere);
  window.webContents.on("will-redirect", stayHere);
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });

  await window.loadURL(`${origin}/`);
}

app.whenReady().then(openWindow).catch((error: unknown) => {
  console.error(error);
  stopServer();
  app.exit(1);
});

app.on("window-all-closed", () => {
  stopServer();
  app.quit();
});
app.on("before-quit", stopServer);
