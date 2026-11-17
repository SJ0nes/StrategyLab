// @vitest-environment node
/**
 * The Electron main process's wiring, run against a stand-in for Electron and
 * for the server process. `electron/policy.ts` decides; these tests check that
 * `electron/main.ts` does what it decides: the server started as the policy
 * says, the token added to the API's requests and nowhere else, the window kept
 * on the server, and the server's lifeline closed whenever the window goes.
 *
 * Nothing here starts Electron or a server. What only a real launch can show
 * (the page's script unable to see the token, no server left after the window
 * closes) is in LIMITATIONS.md.
 */
import { EventEmitter } from "node:events";
import path from "node:path";
import { PassThrough } from "node:stream";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiPatterns, serverArguments, serverEnvironment, TOKEN_VARIABLE } from "../electron/policy";

/** A stand-in for the server process. */
class FakeChild extends EventEmitter {
  stdout = new PassThrough();
  stdin = Object.assign(new EventEmitter(), { end: vi.fn() });
}

/** A stand-in for one Electron window, keeping what main.ts set on it. */
class FakeWindow {
  static made: FakeWindow[] = [];
  options: Record<string, unknown>;
  handlers = new Map<string, (event: { preventDefault: () => void }, url: string) => void>();
  headerFilter: { urls: string[] } | null = null;
  headerListener:
    | ((details: { requestHeaders: Record<string, string> }, callback: (r: unknown) => void) => void)
    | null = null;
  openHandler: ((details: { url: string }) => { action: string }) | null = null;
  loadURL = vi.fn(async (_url: string) => undefined);
  removeMenu = vi.fn();
  webContents = {
    on: (name: string, handler: (event: { preventDefault: () => void }, url: string) => void) => {
      this.handlers.set(name, handler);
    },
    setWindowOpenHandler: (handler: (details: { url: string }) => { action: string }) => {
      this.openHandler = handler;
    },
    session: {
      webRequest: {
        onBeforeSendHeaders: (
          filter: { urls: string[] },
          listener: (details: { requestHeaders: Record<string, string> }, callback: (r: unknown) => void) => void,
        ) => {
          this.headerFilter = filter;
          this.headerListener = listener;
        },
      },
    },
  };

  constructor(options: Record<string, unknown>) {
    this.options = options;
    FakeWindow.made.push(this);
  }
}

const fake = vi.hoisted(() => ({
  lock: true,
  uv: "uv-on-path" as string | null,
  appHandlers: new Map<string, () => void>(),
  ready: null as null | (() => void),
}));

// A call to spawn that no test set up fails loudly rather than starting anything.
const spawn = vi.hoisted(() =>
  vi.fn((..._args: unknown[]): unknown => {
    throw new Error("spawn was called with no stand-in set up");
  }),
);
const app = vi.hoisted(() => ({
  setAppUserModelId: vi.fn(),
  requestSingleInstanceLock: vi.fn(() => fake.lock),
  quit: vi.fn(),
  exit: vi.fn(),
  on: vi.fn((name: string, handler: () => void) => {
    fake.appHandlers.set(name, handler);
  }),
  whenReady: vi.fn(
    () =>
      new Promise<void>((resolve) => {
        fake.ready = resolve;
      }),
  ),
}));
const shell = vi.hoisted(() => ({ openExternal: vi.fn(async () => undefined) }));

vi.mock("node:child_process", () => ({ spawn, default: { spawn } }));
vi.mock("electron", () => ({ app, shell, BrowserWindow: FakeWindow, default: { app, shell, BrowserWindow: FakeWindow } }));
vi.mock("../electron/findOnPath", () => ({ findOnPath: () => fake.uv }));

const REPOSITORY = path.resolve(__dirname, "..", "..");

let child: FakeChild;

/** Load main.ts afresh, as Electron would at launch. */
async function launch(): Promise<void> {
  vi.resetModules();
  // A missed stand-in would start a real server on the repository.
  const { spawn: imported } = await import("node:child_process");
  const { findOnPath } = await import("../electron/findOnPath");
  expect(vi.isMockFunction(imported)).toBe(true);
  expect(findOnPath("uv", {}, "win32", () => true)).toBe(fake.uv);
  await import("../electron/main");
}

/** Tell main.ts that Electron is ready, and let what follows run. */
async function ready(): Promise<void> {
  fake.ready?.();
  await vi.waitFor(() => expect(spawn.mock.calls.length + app.exit.mock.calls.length).toBeGreaterThan(0));
}

/** Have the server print a line. */
function prints(line: string): void {
  child.stdout.write(`${line}\n`);
}

/** Launch, be ready, and have the server report port 5123; resolves with the window. */
async function opened(): Promise<FakeWindow> {
  await launch();
  await ready();
  prints(JSON.stringify({ port: 5123 }));
  await vi.waitFor(() => expect(FakeWindow.made).toHaveLength(1));
  return FakeWindow.made[0]!;
}

/** How spawn was called: program, arguments, options. */
function spawned(): [string, string[], { cwd: string; env: NodeJS.ProcessEnv; stdio: string[] }] {
  expect(spawn).toHaveBeenCalledTimes(1);
  return spawn.mock.calls[0] as unknown as [string, string[], { cwd: string; env: NodeJS.ProcessEnv; stdio: string[] }];
}

/** Whether a handler stopped the window going to a URL. */
function stopped(handler: (event: { preventDefault: () => void }, url: string) => void, url: string): boolean {
  const event = { preventDefault: vi.fn() };
  handler(event, url);
  return event.preventDefault.mock.calls.length > 0;
}

beforeEach(() => {
  fake.lock = true;
  fake.uv = "uv-on-path";
  fake.appHandlers.clear();
  fake.ready = null;
  FakeWindow.made = [];
  vi.clearAllMocks();
  child = new FakeChild();
  spawn.mockImplementation(() => child);
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  delete process.env.STRATEGYLAB_WORKSPACE;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("starting the server", () => {
  it("runs the policy's command, in the repository, with the token in its environment only", async () => {
    await launch();
    await ready();

    const [program, args, options] = spawned();
    expect(program).toBe("uv-on-path");
    expect(args).toEqual(serverArguments(REPOSITORY));
    expect(options.cwd).toBe(REPOSITORY);
    expect(options.stdio[0]).toBe("pipe");
    const launchToken = options.env[TOKEN_VARIABLE]!;
    expect(launchToken).toMatch(/^[0-9a-f]{64}$/);
    expect(options.env).toEqual(serverEnvironment(process.env, launchToken));
    expect(args.join(" ")).not.toContain(launchToken);
  });

  it("runs in the workspace when one is named", async () => {
    const workspace = path.resolve("some", "copy");
    process.env.STRATEGYLAB_WORKSPACE = workspace;
    await launch();
    await ready();

    expect(spawned()[2].cwd).toBe(workspace);
    expect(spawned()[1]).toEqual(serverArguments(REPOSITORY));
  });

  it("opens the window only once the server reports its port, and on that port", async () => {
    await launch();
    await ready();
    prints("starting up");
    prints(JSON.stringify({ port: "5123" }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(FakeWindow.made).toHaveLength(0);

    prints(JSON.stringify({ port: 5123 }));
    await vi.waitFor(() => expect(FakeWindow.made).toHaveLength(1));
    await vi.waitFor(() => expect(FakeWindow.made[0]!.loadURL).toHaveBeenCalledWith("http://127.0.0.1:5123/"));
  });
});

describe("the window", () => {
  it("gives the page no Node and keeps it apart from Electron", async () => {
    const window = await opened();

    expect(window.options.webPreferences).toEqual({ contextIsolation: true, nodeIntegration: false, sandbox: true });
  });

  it("adds the token to the server's API requests only, keeping their other headers", async () => {
    const window = await opened();
    const launchToken = spawned()[2].env[TOKEN_VARIABLE];

    expect(window.headerFilter).toEqual({ urls: apiPatterns(5123) });
    const callback = vi.fn();
    window.headerListener!({ requestHeaders: { Accept: "application/json" } }, callback);
    expect(callback).toHaveBeenCalledWith({
      requestHeaders: { Accept: "application/json", Authorization: `Bearer ${launchToken}` },
    });
  });

  it("stays on the server whether it is sent elsewhere or redirected", async () => {
    const window = await opened();

    for (const name of ["will-navigate", "will-redirect"]) {
      const handler = window.handlers.get(name)!;
      expect(handler, name).toBeDefined();
      expect(stopped(handler, "https://example.com/"), name).toBe(true);
      expect(stopped(handler, "http://127.0.0.1:51234/"), name).toBe(true);
      expect(stopped(handler, "http://127.0.0.1:5123/tests"), name).toBe(false);
    }
  });

  it("never opens a second window, and sends only https links to the browser", async () => {
    const window = await opened();

    expect(window.openHandler!({ url: "https://example.com/" })).toEqual({ action: "deny" });
    expect(shell.openExternal).toHaveBeenCalledWith("https://example.com/");
    shell.openExternal.mockClear();
    for (const url of ["http://example.com/", "file:///C:/Windows/", "http://127.0.0.1:5123/"]) {
      expect(window.openHandler!({ url })).toEqual({ action: "deny" });
    }
    expect(shell.openExternal).not.toHaveBeenCalled();
  });
});

describe("when the server cannot be started", () => {
  it("stops, and starts nothing, when uv is not on PATH", async () => {
    fake.uv = null;
    await launch();
    await ready();

    expect(spawn).not.toHaveBeenCalled();
    expect(app.exit).toHaveBeenCalledWith(1);
    expect(FakeWindow.made).toHaveLength(0);
  });

  it("stops, and closes the lifeline, when the process cannot run", async () => {
    await launch();
    await ready();
    child.emit("error", new Error("cannot run"));

    await vi.waitFor(() => expect(app.exit).toHaveBeenCalledWith(1));
    expect(child.stdin.end).toHaveBeenCalled();
    expect(FakeWindow.made).toHaveLength(0);
  });

  it("is not stopped by a broken pipe when the lifeline of a server already gone is closed", async () => {
    await launch();
    await ready();
    child.emit("exit", 1);
    await vi.waitFor(() => expect(app.exit).toHaveBeenCalledWith(1));

    expect(() => child.stdin.emit("error", Object.assign(new Error("write EPIPE"), { code: "EPIPE" }))).not.toThrow();
  });

  it("stops when the server exits before it is ready", async () => {
    await launch();
    await ready();
    child.emit("exit", 2);

    await vi.waitFor(() => expect(app.exit).toHaveBeenCalledWith(1));
    expect(child.stdin.end).toHaveBeenCalled();
    expect(FakeWindow.made).toHaveLength(0);
  });

  it("stops, and closes the lifeline, when no port comes within a minute", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await launch();
    fake.ready?.();
    while (spawn.mock.calls.length === 0) {
      await new Promise((resolve) => setImmediate(resolve));
    }

    await vi.advanceTimersByTimeAsync(59_999);
    await new Promise((resolve) => setImmediate(resolve));
    expect(app.exit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    vi.useRealTimers();

    await vi.waitFor(() => expect(app.exit).toHaveBeenCalledWith(1));
    expect(child.stdin.end).toHaveBeenCalled();
  });
});

describe("closing", () => {
  it("closes the server's lifeline when the last window closes, and quits", async () => {
    await opened();
    fake.appHandlers.get("window-all-closed")!();

    expect(child.stdin.end).toHaveBeenCalledTimes(1);
    expect(app.quit).toHaveBeenCalled();
  });

  it("closes the lifeline once only, however many ways the app is told to go", async () => {
    await opened();
    fake.appHandlers.get("before-quit")!();
    fake.appHandlers.get("window-all-closed")!();
    fake.appHandlers.get("before-quit")!();

    expect(child.stdin.end).toHaveBeenCalledTimes(1);
  });
});

describe("one window at a time", () => {
  it("shows under the desktop shortcut's id", async () => {
    await launch();

    expect(app.setAppUserModelId).toHaveBeenCalledWith("StrategyLab");
  });

  it("quits, and starts no server, when another window is already open", async () => {
    fake.lock = false;
    await launch();
    fake.ready?.();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(app.quit).toHaveBeenCalled();
    expect(app.whenReady).not.toHaveBeenCalled();
    expect(spawn).not.toHaveBeenCalled();
    expect(FakeWindow.made).toHaveLength(0);
  });
});
