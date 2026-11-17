/**
 * Launch the real window on a throwaway copy of the project and check what the
 * tests against a stand-in for Electron cannot:
 *
 *   1. the page's script has no Node: no `require`, `process` or `module`;
 *   2. no 64-character hex string, the launch token's shape, is in the page's
 *      address, cookies, storage, markup, loaded addresses, or any string
 *      property of `window`. A value held inside the page's own code is not
 *      searched;
 *   3. the page's own request to the API is let in, so the token reached the
 *      server, while the same request from outside the window is refused;
 *   4. once the window closes, the server stops, and (on Windows) no process
 *      the window started is left. One that is left is reported and stopped.
 *      Only processes descended from the window this script started are ever
 *      stopped.
 *
 * Run `npm run build` first, then `npm run smoke`, from `desktop/`. It needs
 * `uv` on PATH, as the window does. The server runs this repository's code, so
 * uv may create or update the repository's `.venv/`, as `npm start` does; the
 * project files it serves are a copy holding `protocol.yaml`, `configs/` and the
 * built page, deleted at the end. The window is started without any Trading 212
 * variable, so nothing it does can reach the practice account. Pressing no
 * button, it starts no job.
 *
 * While it runs, the window listens for a debugger on a local port the system
 * chooses; this is how the script reads the page. Any program on this machine
 * could attach to it for that time, so run it on a machine you trust. The port
 * closes with the window.
 *
 * Exits 0 when every check passes, 1 when any fails, 2 when it could not run.
 */
import { spawn, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const DESKTOP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPOSITORY = path.resolve(DESKTOP, "..");
/** How long the window may take to show the page, in milliseconds. */
const OPEN_TIMEOUT_MS = 120_000;
/** How long the server may take to go once the window closes, in milliseconds. */
const CLOSE_TIMEOUT_MS = 20_000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** A message with the repository and temporary folders named generically, so no home path is printed. */
function withoutHome(text) {
  return String(text).split(REPOSITORY).join("<repository>").split(tmpdir()).join("<temp>");
}

/** Copy what the server needs to serve the project into a new directory. */
function makeWorkspace() {
  const workspace = mkdtempSync(path.join(tmpdir(), "strategylab-smoke-"));
  cpSync(path.join(REPOSITORY, "protocol.yaml"), path.join(workspace, "protocol.yaml"));
  cpSync(path.join(REPOSITORY, "configs"), path.join(workspace, "configs"), { recursive: true });
  cpSync(path.join(DESKTOP, "dist"), path.join(workspace, "desktop", "dist"), { recursive: true });
  mkdirSync(path.join(workspace, "state"));
  return workspace;
}

/** The main process's environment, less every Trading 212 variable, serving `workspace`. */
function windowEnvironment(workspace) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.toUpperCase().startsWith("T212_")));
  return { ...env, STRATEGYLAB_WORKSPACE: workspace };
}

/**
 * The debugger address the window itself announces. Read from the window's own
 * output, so it cannot be another program's debugger on a port that happened
 * to be free a moment before.
 */
function announcedDebugger(app) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("the window announced no debugger in time")), OPEN_TIMEOUT_MS);
    app.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`the window exited (code ${code}) before it opened; is another StrategyLab window open?`));
    });
    readline.createInterface({ input: app.stderr }).on("line", (line) => {
      const found = /DevTools listening on (ws:\/\/127\.0\.0\.1:(\d+)\/\S+)/.exec(line);
      if (found !== null) {
        clearTimeout(timer);
        resolve({ browser: found[1], port: Number(found[2]) });
      }
    });
  });
}

/** Wait for the window's page on the local server; resolves with its debugger target. */
async function pageTarget(debugPort, app) {
  const deadline = Date.now() + OPEN_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (app.exitCode !== null) {
      throw new Error(`the window exited (code ${app.exitCode}) before showing a page`);
    }
    try {
      const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
      const page = targets.find((target) => target.type === "page" && target.url.startsWith("http://127.0.0.1:"));
      if (page !== undefined) {
        return page;
      }
    } catch {
      // The page is not up yet.
    }
    await sleep(500);
  }
  throw new Error("the window showed no page in time");
}

/** Open a debugger session on a target; resolves with a function that sends one command. */
async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let next = 0;
  const waiting = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    const settle = waiting.get(message.id);
    if (settle !== undefined) {
      waiting.delete(message.id);
      settle(message);
    }
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      next += 1;
      waiting.set(next, (message) =>
        message.error === undefined ? resolve(message.result) : reject(new Error(`${method}: ${message.error.message}`)),
      );
      socket.send(JSON.stringify({ id: next, method, params }));
    });
  return { send, close: () => socket.close() };
}

/**
 * Run an expression in the page and return its value. The page may reload while
 * it starts, which ends the expression; it is then run again, up to five times.
 */
async function inPage(send, expression) {
  let result;
  for (let attempt = 1; ; attempt += 1) {
    try {
      result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      break;
    } catch (error) {
      if (attempt === 5 || !String(error).includes("context was destroyed")) {
        throw error;
      }
      await sleep(1_000);
    }
  }
  if (result.exceptionDetails !== undefined) {
    throw new Error(`the page could not run a check: ${result.exceptionDetails.exception?.description ?? result.exceptionDetails.text}`);
  }
  return result.result.value;
}

/**
 * Whether the server's port still takes connections. Only a refused connection
 * counts as stopped: a server that hangs without answering is still there.
 */
async function answers(origin) {
  try {
    await fetch(`${origin}/api/health`, { signal: AbortSignal.timeout(2_000) });
    return true;
  } catch (error) {
    return error?.cause?.code !== "ECONNREFUSED";
  }
}

/**
 * Every running process as `{ id, parent, created }`, or null when they cannot
 * be listed. Windows only; elsewhere null.
 */
function processTable() {
  if (process.platform !== "win32") {
    return null;
  }
  const query =
    "Get-CimInstance Win32_Process | ForEach-Object { " +
    '\"$($_.ProcessId) $($_.ParentProcessId) $($_.CreationDate.ToFileTimeUtc())\" }';
  const done = spawnSync("powershell", ["-NoProfile", "-Command", query], { encoding: "utf-8", windowsHide: true });
  if (done.status !== 0 || typeof done.stdout !== "string") {
    return null;
  }
  return done.stdout
    .split(/\r?\n/)
    .map((line) => line.trim().split(" "))
    .filter((fields) => fields.length === 3 && fields.every((field) => /^\d+$/.test(field)))
    .map(([id, parent, created]) => ({ id: Number(id), parent: Number(parent), created }));
}

/** The processes descended from `root`, each with its start time so a reused id is not mistaken for it. */
function descendants(table, root) {
  const found = [];
  const queue = [root];
  while (queue.length > 0) {
    const parent = queue.shift();
    for (const entry of table) {
      if (entry.parent === parent && entry.id !== parent && !found.some((known) => known.id === entry.id)) {
        found.push(entry);
        queue.push(entry.id);
      }
    }
  }
  return found;
}

/** Of `started`, the ones still running: the same id and the same start time. */
function stillRunning(started) {
  const table = processTable() ?? [];
  return started.filter((entry) => table.some((now) => now.id === entry.id && now.created === entry.created));
}

const results = [];
function check(name, passed, detail) {
  results.push(passed);
  console.log(`${passed ? "PASS" : "FAIL"}  ${name}${detail === undefined ? "" : ` (${detail})`}`);
}

async function main() {
  for (const built of [path.join(DESKTOP, "dist", "index.html"), path.join(DESKTOP, "dist-electron", "main.js")]) {
    if (!existsSync(built)) {
      console.error(`desktop/${path.relative(DESKTOP, built).split(path.sep).join("/")} is missing; run \`npm run build\` first.`);
      return 2;
    }
  }
  const electron = createRequire(import.meta.url)("electron");
  const workspace = makeWorkspace();
  const app = spawn(electron, [DESKTOP, "--remote-debugging-port=0"], {
    env: windowEnvironment(workspace),
    stdio: ["ignore", "ignore", "pipe"],
    windowsHide: true,
  });
  const exited = new Promise((resolve) => app.once("exit", resolve));
  let started = [];
  try {
    const debug = await announcedDebugger(app);
    const page = await pageTarget(debug.port, app);
    const origin = new URL(page.url).origin;
    const { send, close } = await connect(page.webSocketDebuggerUrl);
    await inPage(
      send,
      // A page whose requests are refused may never draw its heading; the checks still run.
      `new Promise((resolve) => { const start = Date.now(); const ready = () => document.querySelector("h1") !== null || Date.now() - start > 30000 ? resolve(true) : setTimeout(ready, 100); ready(); })`,
    );
    const table = processTable();
    started = table === null ? [] : descendants(table, app.pid);

    const node = await inPage(send, `[typeof require, typeof process, typeof module].join(" ")`);
    check("the page's script has no Node", node === "undefined undefined undefined", node);

    const seen = await inPage(
      send,
      `(() => {
        const places = [location.href, document.cookie, document.documentElement.outerHTML, JSON.stringify(localStorage),
          JSON.stringify(sessionStorage), JSON.stringify(performance.getEntries().map((entry) => entry.name))];
        for (const name of Object.getOwnPropertyNames(window)) {
          try { const value = window[name]; if (typeof value === "string") places.push(value); } catch {}
        }
        return places.filter((place) => /[0-9a-f]{64}/.test(place)).length;
      })()`,
    );
    check("no token-shaped string in the page's address, storage, markup or window strings", seen === 0, `${seen} places hold one`);

    const inside = await inPage(send, `fetch("/api/overview").then((response) => response.status)`);
    check("the page's own API request is let in", inside === 200, `status ${inside}`);
    const outside = (await fetch(`${origin}/api/overview`)).status;
    check("the same request from outside the window is refused", outside === 401, `status ${outside}`);

    close();
    const browser = await connect(debug.browser);
    void browser.send("Browser.close").catch(() => undefined);
    const closed = await Promise.race([exited.then(() => true), sleep(CLOSE_TIMEOUT_MS).then(() => false)]);
    check("the window closes", closed);

    const deadline = Date.now() + CLOSE_TIMEOUT_MS;
    let serving = await answers(origin);
    let left = stillRunning(started);
    while ((serving || left.length > 0) && Date.now() < deadline) {
      await sleep(500);
      serving = await answers(origin);
      left = stillRunning(started);
    }
    check("the server stops once the window closes", !serving, origin);
    if (table === null) {
      console.log("SKIP  no process the window started is left (processes are listed on Windows only)");
    } else {
      check("no process the window started is left", left.length === 0, `${started.length} started, ${left.length} left`);
    }
  } finally {
    if (app.exitCode === null) {
      app.kill();
      await exited;
    }
    // Whatever the window started and left behind serves a copy about to be deleted.
    for (const entry of stillRunning(started)) {
      spawnSync("taskkill", ["/PID", String(entry.id), "/F"], { stdio: "ignore", windowsHide: true });
      console.log(`stopped process ${entry.id}, started by the window and left running`);
    }
    for (let attempt = 0; attempt < 10 && existsSync(workspace); attempt += 1) {
      try {
        rmSync(workspace, { recursive: true, force: true });
      } catch {
        await sleep(1_000);
      }
    }
    if (existsSync(workspace)) {
      console.log(`the copy could not be deleted: ${withoutHome(workspace)}`);
    }
  }
  return results.every(Boolean) ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(withoutHome(error instanceof Error ? error.message : error));
    process.exit(2);
  },
);
