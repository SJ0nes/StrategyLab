/**
 * The decisions the Electron main process makes, kept apart from the wiring so
 * that each can be tested without starting Electron or a server.
 *
 * Nothing here starts a process, opens a window or reads a file. `main.ts`
 * calls these functions and does what they say.
 */
import path from "node:path";

/**
 * Names a directory to serve in place of the repository: a copy of the project
 * made to try the window on, so that pressing a button there starts nothing
 * here. See `main.ts`.
 */
export const WORKSPACE_VARIABLE = "STRATEGYLAB_WORKSPACE";

/** The environment variable the server reads its token from. */
export const TOKEN_VARIABLE = "STRATEGYLAB_APP_TOKEN";

/**
 * The directory the server runs in.
 *
 * @param env The main process's environment.
 * @param repository The repository root, absolute.
 * @returns The workspace named by `STRATEGYLAB_WORKSPACE`, made absolute, or
 *   the repository when that variable is unset or empty.
 */
export function servedDirectory(env: NodeJS.ProcessEnv, repository: string): string {
  const workspace = env[WORKSPACE_VARIABLE];
  return workspace === undefined || workspace === "" ? repository : path.resolve(workspace);
}

/**
 * The arguments `uv` is started with to run the server.
 *
 * `--frozen` runs the project exactly as its lockfile says. `--project` points
 * at the repository, so the code that runs is always the repository's, whatever
 * directory is served. `--port 0` lets the operating system choose a free port.
 *
 * @param repository The repository root, absolute.
 * @returns The argument list, in order.
 */
export function serverArguments(repository: string): string[] {
  return ["run", "--frozen", "--project", repository, "strategylab", "serve", "--port", "0"];
}

/**
 * The environment the server is started with: the main process's own, plus
 * the launch token. The token goes here, not on the command line, where any
 * process on the machine could read it.
 *
 * @param env The main process's environment. Not changed.
 * @param launchToken The token made for this launch.
 * @returns A new environment.
 */
export function serverEnvironment(env: NodeJS.ProcessEnv, launchToken: string): NodeJS.ProcessEnv {
  return { ...env, [TOKEN_VARIABLE]: launchToken };
}

/**
 * Read the port from one line the server printed, if the line is the ready
 * line.
 *
 * @param line One line of the server's standard output.
 * @returns The port, a whole number from 1 to 65535, when the line is a JSON
 *   object whose `port` is such a number; otherwise null. The server may print
 *   other things, and they are ignored.
 */
export function readyPort(line: string): number | null {
  let message: unknown;
  try {
    message = JSON.parse(line);
  } catch {
    return null;
  }
  if (typeof message !== "object" || message === null || !("port" in message)) {
    return null;
  }
  const port = (message as { port: unknown }).port;
  if (typeof port !== "number" || !Number.isInteger(port) || port < 1 || port > 65535) {
    return null;
  }
  return port;
}

/**
 * The address of the local server.
 *
 * @param port The port the server reported.
 * @returns The origin, with no trailing slash.
 */
export function serverOrigin(port: number): string {
  return `http://127.0.0.1:${port}`;
}

/**
 * The request patterns that carry the token: the server's API over HTTP and
 * over a WebSocket. Nothing else, so the token never goes anywhere but this
 * server's API.
 *
 * @param port The port the server reported.
 * @returns Electron URL patterns.
 */
export function apiPatterns(port: number): string[] {
  return [`${serverOrigin(port)}/api/*`, `ws://127.0.0.1:${port}/api/*`];
}

/**
 * Add the token to a request's headers.
 *
 * @param headers The request's headers. Not changed.
 * @param launchToken The token made for this launch.
 * @returns A new set of headers: the old ones, with `Authorization` set to
 *   `Bearer <token>`.
 */
export function withToken(headers: Record<string, string>, launchToken: string): Record<string, string> {
  return { ...headers, Authorization: `Bearer ${launchToken}` };
}

/**
 * Whether the window may navigate to a URL. It shows the local server and
 * nothing else.
 *
 * @param url Where the window is about to go.
 * @param origin The server's origin, from {@link serverOrigin}.
 * @returns True only for a URL on that origin. The trailing slash in the test
 *   keeps `http://127.0.0.1:5000` from admitting `http://127.0.0.1:50001/`.
 */
export function mayNavigate(url: string, origin: string): boolean {
  return url.startsWith(`${origin}/`);
}

/**
 * Whether a link the page tries to open in a new window goes to the person's
 * own browser. The window itself never opens a second window.
 *
 * @param url The link.
 * @returns True only for an `https://` link.
 */
export function opensExternally(url: string): boolean {
  return url.startsWith("https://");
}
