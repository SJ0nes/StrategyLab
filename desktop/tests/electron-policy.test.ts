import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  TOKEN_VARIABLE,
  apiPatterns,
  mayNavigate,
  opensExternally,
  readyPort,
  serverArguments,
  serverEnvironment,
  serverOrigin,
  servedDirectory,
  withToken,
} from "../electron/policy";

const REPOSITORY = path.resolve("repository-root");

describe("which directory the server runs in", () => {
  it("is the repository when no workspace is named", () => {
    expect(servedDirectory({}, REPOSITORY)).toBe(REPOSITORY);
    expect(servedDirectory({ STRATEGYLAB_WORKSPACE: "" }, REPOSITORY)).toBe(REPOSITORY);
  });

  it("is the named workspace, made absolute", () => {
    const named = path.resolve("elsewhere", "copy");
    expect(servedDirectory({ STRATEGYLAB_WORKSPACE: named }, REPOSITORY)).toBe(named);
    expect(servedDirectory({ STRATEGYLAB_WORKSPACE: "copy" }, REPOSITORY)).toBe(path.resolve("copy"));
  });
});

describe("how the server is started", () => {
  it("runs the repository's own code, locked, on a port the system chooses", () => {
    expect(serverArguments(REPOSITORY)).toEqual([
      "run",
      "--frozen",
      "--project",
      REPOSITORY,
      "strategylab",
      "serve",
      "--port",
      "0",
    ]);
  });

  it("hands the token over in the environment, keeping the rest of it", () => {
    const env = { PATH: "C:\\bin", T212_ENVIRONMENT: "demo" };

    const made = "launch-token";
    const started = serverEnvironment(env, made);

    expect(started).toEqual({ PATH: "C:\\bin", T212_ENVIRONMENT: "demo", STRATEGYLAB_APP_TOKEN: made });
    expect(TOKEN_VARIABLE).toBe("STRATEGYLAB_APP_TOKEN");
    expect(env).toEqual({ PATH: "C:\\bin", T212_ENVIRONMENT: "demo" });
  });

  it("never puts the token in the arguments", () => {
    expect(serverArguments(REPOSITORY).join(" ")).not.toContain("launch-token");
  });
});

describe("reading the ready line", () => {
  it("takes the port from a JSON object", () => {
    expect(readyPort('{"port": 51234}')).toBe(51234);
    expect(readyPort('{"ready": true, "port": 1}')).toBe(1);
    expect(readyPort('{"port": 65535}')).toBe(65535);
  });

  it("ignores anything else the server prints", () => {
    expect(readyPort("INFO: started")).toBeNull();
    expect(readyPort("")).toBeNull();
    expect(readyPort("51234")).toBeNull();
    expect(readyPort("null")).toBeNull();
    expect(readyPort('{"ready": true}')).toBeNull();
    expect(readyPort('{"port": "51234"}')).toBeNull();
  });

  it("refuses a port no server can listen on", () => {
    expect(readyPort('{"port": 0}')).toBeNull();
    expect(readyPort('{"port": -1}')).toBeNull();
    expect(readyPort('{"port": 65536}')).toBeNull();
    expect(readyPort('{"port": 5000.5}')).toBeNull();
  });
});

describe("what the window may show", () => {
  const origin = serverOrigin(5000);

  it("is the local server", () => {
    expect(origin).toBe("http://127.0.0.1:5000");
    expect(mayNavigate("http://127.0.0.1:5000/", origin)).toBe(true);
    expect(mayNavigate("http://127.0.0.1:5000/trades?x=1", origin)).toBe(true);
  });

  it("is nothing else, not even a longer port that starts the same", () => {
    expect(mayNavigate("http://127.0.0.1:50001/", origin)).toBe(false);
    expect(mayNavigate("http://127.0.0.1:5000", origin)).toBe(false);
    expect(mayNavigate("http://127.0.0.1:5000.example.com/", origin)).toBe(false);
    expect(mayNavigate("http://localhost:5000/", origin)).toBe(false);
    expect(mayNavigate("https://example.com/", origin)).toBe(false);
    expect(mayNavigate("file:///C:/Windows/", origin)).toBe(false);
  });

  it("opens only https links in the person's own browser", () => {
    expect(opensExternally("https://www.trading212.com/")).toBe(true);
    expect(opensExternally("http://example.com/")).toBe(false);
    expect(opensExternally("file:///C:/Windows/")).toBe(false);
    expect(opensExternally("javascript:alert(1)")).toBe(false);
    expect(opensExternally(" https://example.com/")).toBe(false);
  });
});

describe("where the token goes", () => {
  it("is the server's API over HTTP and WebSocket, and nowhere else", () => {
    expect(apiPatterns(5000)).toEqual(["http://127.0.0.1:5000/api/*", "ws://127.0.0.1:5000/api/*"]);
  });

  it("is added to a request's headers, keeping the others", () => {
    const headers = { Accept: "application/json" };

    expect(withToken(headers, "launch-token")).toEqual({
      Accept: "application/json",
      Authorization: "Bearer launch-token",
    });
    expect(headers).toEqual({ Accept: "application/json" });
  });

  it("replaces an Authorization header the page set itself", () => {
    expect(withToken({ Authorization: "Bearer forged" }, "launch-token")).toEqual({
      Authorization: "Bearer launch-token",
    });
  });
});
