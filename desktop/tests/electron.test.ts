import { describe, expect, it } from "vitest";

import { findOnPath } from "../electron/findOnPath";

const on = (...files: string[]) => (candidate: string) => files.includes(candidate);

describe("finding a program on PATH", () => {
  it("returns the first directory on PATH that holds it", () => {
    const env = { PATH: "C:\\one;C:\\two", PATHEXT: ".EXE" };

    expect(findOnPath("uv", env, "win32", on("C:\\two\\uv.EXE"))).toBe("C:\\two\\uv.EXE");
    expect(
      findOnPath("uv", env, "win32", on("C:\\one\\uv.EXE", "C:\\two\\uv.EXE")),
    ).toBe("C:\\one\\uv.EXE");
  });

  it("never looks in the current directory, however PATH names it", () => {
    const anywhere = () => true;

    expect(findOnPath("uv", { PATH: "", PATHEXT: ".EXE" }, "win32", anywhere)).toBeNull();
    expect(findOnPath("uv", { PATH: ".;;relative\\bin", PATHEXT: ".EXE" }, "win32", anywhere)).toBeNull();
    expect(findOnPath("uv", { PATH: ".::relative/bin" }, "linux", anywhere)).toBeNull();
  });

  it("tries each extension Windows would, and none elsewhere", () => {
    const env = { PATH: "C:\\bin", PATHEXT: ".EXE;.CMD" };

    expect(findOnPath("uv", env, "win32", on("C:\\bin\\uv.CMD"))).toBe("C:\\bin\\uv.CMD");
    expect(findOnPath("uv", { PATH: "/usr/bin:/opt/bin" }, "linux", on("/opt/bin/uv"))).toBe("/opt/bin/uv");
  });

  it("says so when the program is nowhere", () => {
    expect(findOnPath("uv", { PATH: "C:\\bin", PATHEXT: ".EXE" }, "win32", on())).toBeNull();
  });
});
