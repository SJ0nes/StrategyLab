import path from "node:path";

/**
 * Find a program on PATH and return its full path, or null.
 *
 * Done by hand so that the directory the server runs in is never searched.
 * Left to the operating system, a file named `uv.exe` dropped into the
 * repository would be run in place of the real one, with the token and any
 * venue key in its environment.
 */
export function findOnPath(
  program: string,
  env: NodeJS.ProcessEnv,
  platform: NodeJS.Platform,
  exists: (candidate: string) => boolean,
): string | null {
  const windows = platform === "win32";
  const separator = windows ? ";" : ":";
  const join = windows ? path.win32.join : path.posix.join;
  const isAbsolute = windows ? path.win32.isAbsolute : path.posix.isAbsolute;
  const extensions = windows ? (env.PATHEXT ?? ".EXE;.CMD;.BAT").split(";").filter(Boolean) : [""];
  for (const directory of (env.PATH ?? env.Path ?? "").split(separator)) {
    // A relative entry, the empty one included, means "the current directory".
    if (directory === "" || !isAbsolute(directory)) {
      continue;
    }
    for (const extension of extensions) {
      const candidate = join(directory, program + extension);
      if (exists(candidate)) {
        return candidate;
      }
    }
  }
  return null;
}
