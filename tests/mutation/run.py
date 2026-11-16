"""Break the code one line at a time and check that a test notices.

Run from the repository root:

    uv run python tests/mutation/run.py            # every group
    uv run python tests/mutation/run.py hook cli   # only these groups

WHAT THIS IS FOR. ``CLAUDE.md`` says a test that cannot fail is not a test, and
that before trusting a new assertion the code it covers should be broken to see
the test go red. This does that mechanically. Each entry in ``mutants.py`` is
one deliberate defect - a comparison flipped, a check removed, a field dropped -
with the test files that ought to catch it. The harness applies one, runs those
tests, restores the file, and reports whether anything failed.

WHAT IT DOES NOT PROVE, and ``docs/decisions/0006`` is the longer version. A
mutant caught shows that a test notices when the implementation changes. It
says nothing about whether the expected value in that test is right: if a
target was derived wrongly and the code was written to agree with it, every
mutation is still caught and the number is still wrong. And it cannot see a
case no test exercises at all. A clean run here is a floor, not a verdict.

TWO WAYS A HARNESS LIKE THIS LIES, both of which earlier, uncommitted versions
of it did. One grepped test output for a summary line that a quiet flag had
suppressed, and so reported every mutant caught. Another decided a mutation had
applied by comparing bytes, and a tool that rewrote line endings made every
pattern look as if it had matched. So this one counts how many times each
pattern occurs and refuses anything but exactly one, reads the mutated text
back before running, and judges by the test run's exit code and nothing else.

A THIRD WAY, found later in a scratch harness built the same way. Python keeps
compiled copies of each module in ``__pycache__`` and trusts one whose recorded
source size and modification second match the file. A mutant that keeps the
line's length - ``<`` for ``>`` - written and restored within one second leaves
a compiled copy of the MUTANT that matches the restored file, and every later
run imports the defect: the next mutant can look caught because of the last
one. So the test runs here write no compiled copies, and the old ones under
``src/`` and ``tests/`` are deleted before anything is mutated.

IT EDITS SOURCE FILES IN PLACE. Each is restored in a ``finally``, but a run
killed outright can leave one mutant behind. ``git status`` after an
interrupted run says whether it did, and ``git checkout`` of that file fixes it.
Do not run the test suite, or commit, while this is running.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mutants import GROUPS  # noqa: E402 - the import needs the path set above


def run_tests(tests: tuple[str, ...]) -> int:
    """Run the named test files and return pytest's exit code.

    Args:
        tests: Test file paths, relative to the repository root.

    Returns:
        Zero when every test passed, non-zero otherwise.
    """
    done = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", *tests, "-q", "-x", "-p", "no:cacheprovider"],
        cwd=REPOSITORY,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return done.returncode


def forget_compiled() -> None:
    """Delete every ``__pycache__`` under ``src/`` and ``tests/``.

    None is written again while this runs, so a compiled copy of a mutant can
    never outlive the mutant.
    """
    for top in ("src", "tests"):
        for cache in (REPOSITORY / top).rglob("__pycache__"):
            shutil.rmtree(cache)


def run_group(name: str) -> int:
    """Apply every mutant in one group, one at a time, and report each.

    Args:
        name: A key of ``GROUPS``.

    Returns:
        How many mutants were NOT caught. A pattern that does not occur exactly
        once counts as one, because a mutant that never applied proves nothing.

    Raises:
        SystemExit: If the group's tests fail before anything is mutated, since
            every mutant would then look caught.
    """
    tests, mutants = GROUPS[name]
    if run_tests(tests) != 0:
        raise SystemExit(f"{name}: the tests fail unmutated, so nothing below would mean anything")

    survivors = 0
    for label, target, old, new in mutants:
        path = REPOSITORY / target
        original = path.read_bytes()
        text = original.decode("utf-8")
        if "\r\n" in text:
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        if text.count(old) != 1:
            print(f"  NOT APPLIED  {label}: the pattern occurs {text.count(old)} times in {target}")
            survivors += 1
            continue
        try:
            path.write_bytes(text.replace(old, new).encode("utf-8"))
            if new and new not in path.read_bytes().decode("utf-8"):
                raise SystemExit(f"{label}: the mutated text is not in {target} after writing it")
            caught = run_tests(tests) != 0
        finally:
            path.write_bytes(original)
        survivors += not caught
        print(f"  {'caught      ' if caught else 'SURVIVED    '} {label}")
    return survivors


def main(names: list[str]) -> int:
    """Run the named groups, or all of them, and return a process exit code.

    Args:
        names: Group names from the command line. Empty means every group.

    Returns:
        Zero when every mutant in every group was caught, one otherwise.
    """
    unknown = [name for name in names if name not in GROUPS]
    if unknown:
        raise SystemExit(f"no such group(s) {unknown}; there are {sorted(GROUPS)}")
    forget_compiled()
    total = 0
    for name in names or sorted(GROUPS):
        print(f"{name}: {len(GROUPS[name][1])} mutants against {', '.join(GROUPS[name][0])}")
        total += run_group(name)
    print(f"survivors: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
