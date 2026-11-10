"""Refuse a commit that would put a credential into this public repository's history.

PreToolUse hook on the shell tools. ``commit-gate.ps1`` proves ``.gitignore`` guards
``.env``; this reads what is about to be committed. Two checks, both offline:

  1. Changed paths that look like credential files.
  2. Changed content matching a known credential shape, or a Trading 212 key assigned a
     value. Trading 212 publishes no key format, so that rule goes by the variable's name
     and a key pasted under any other name is not caught here.

"Changed" means staged AND modified-but-unstaged tracked files, in both their index and
working versions. ``git commit -a`` and ``git commit <path>`` take files the index does
not yet hold, so reading only ``git diff --cached`` would let them through.

When the command also says ``add``, untracked files that are not ignored are read too.
The hook runs BEFORE the command, so in ``git add new.py && git commit`` the new file
is still untracked when it is scanned, and neither diff lists it.

A command is parsed to find which repository it commits in (``git -C <dir> commit``,
``git --git-dir <dir> --work-tree <dir> commit``, ``git -c x=y commit``,
``bash -c '...'``). When the words appear but the parser finds no
commit - a multi-line script, an environment prefix, a wrapper - the working directory is
scanned anyway. Scanning a command that turns out not to commit costs one git call;
missing one that does is permanent.

A matched value is never printed, only the file and the rule, because hook output is
written into the session transcript.

Exit codes: 0 allows, 2 blocks. Any other non-zero exit is logged and ignored and the
command proceeds, so every fault in this guard is converted to 2.
"""

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

PATH_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("service account key", re.compile(r"service-account.*\.json$", re.I)),
    ("token file", re.compile(r"(^|/)[^/]*token[^/]*\.(txt|json)$", re.I)),
    ("dotenv file", re.compile(r"(^|/)\.env(\.(?!example$)[^/]*)?$", re.I)),
    ("private key file", re.compile(r"\.(pem|p12|pfx|key)$", re.I)),
    ("key or secret file", re.compile(r"(^|/)[^/]*(api_?key|secret)[^/]*\.(txt|json)$", re.I)),
    ("password file", re.compile(r"(^|/)[^/]*(passphrase|passw(or)?d)[^/]*\.(txt|json)$", re.I)),
]

CONTENT_RULES: list[tuple[str, re.Pattern[bytes]]] = [
    ("PEM private key block", re.compile(rb"BEGIN [A-Z ]*PRIVATE KEY")),
    ("service account private_key field", re.compile(rb'"private_key"\s*:\s*"-----BEGIN')),
    ("Anthropic API key", re.compile(rb"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("Google API key", re.compile(rb"\bAIza[A-Za-z0-9_\-]{30,}")),
    ("GitHub token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    # Horizontal whitespace only: `\s` would step over the newline after an empty value in
    # .env.example and read the next line as the secret.
    (
        "Trading 212 key with a value",
        re.compile(rb"T212_API_(KEY|SECRET)[ \t]*[:=][ \t]*[\"']?[A-Za-z0-9_\-+/.=]{8,}", re.I),
    ),
]

# Named one by one: an exemption on a folder would cover whatever is later parked in it.
# setup.md holds a deliberately fake key, the fixture for proving the guards fire.
CONTENT_EXEMPT: tuple[str, ...] = (".claude/hooks/check_secrets.py", ".claude/setup.md")

LOOKS_LIKE_COMMIT = re.compile(r"\bgit(\.exe)?\b[\s\S]*\bcommit\b")
GIT_BASH_DRIVE = re.compile(r"^/([a-zA-Z])(/|$)")

# git's own options that take a separate value, or the value is read as the subcommand.
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
SHELLS = {"bash", "sh", "zsh", "pwsh", "powershell", "cmd"}

# Bytes. Past this a file is refused unread rather than half-checked.
MAX_SCAN_BYTES = 50_000_000


def git(root: Path, *args: str) -> bytes | None:
    """Run git in ``root``; return stdout, or None if git could not answer."""
    try:
        done = subprocess.run(["git", *args], cwd=str(root), capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def tokenise(command: str) -> list[str] | None:
    """Split a shell command into tokens, or None if its quotes do not balance."""
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def commits(tokens: list[str]) -> list[list[str]]:
    """Return git's own options, before the subcommand, for every commit in ``tokens``.

    Only the options before ``commit`` are returned: after it, ``-C`` means "reuse this
    commit's message", not "run in this directory". A commit message containing the
    words stays inside one quoted token and is not read as a command.
    """
    found: list[list[str]] = []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if set(token) <= set("();&|"):
            segments.append([])
        else:
            segments[-1].append(token)

    for segment in segments:
        if not segment:
            continue
        name = Path(segment[0].strip("'\"")).stem.lower()
        if name in SHELLS:
            for token in segment[1:]:
                if len(token) >= 2 and token[0] in "'\"" and token[-1] == token[0]:
                    inner = tokenise(token[1:-1])
                    if inner is not None:
                        found += commits(inner)
        elif name == "git":
            rest = segment[1:]
            i = 0
            while i < len(rest) and rest[i].startswith("-"):
                i += 2 if rest[i] in GIT_OPTS_WITH_VALUE else 1
            if i < len(rest) and rest[i] == "commit":
                found.append(rest[:i])
    return found


def option_values(options: list[str]) -> dict[str, str]:
    """Map each of git's own options to its value, in either ``--opt v`` or ``--opt=v`` form."""
    values: dict[str, str] = {}
    i = 0
    while i < len(options):
        token = options[i]
        if "=" in token and token.startswith("--"):
            name, value = token.split("=", 1)
            values[name] = value
        elif token in GIT_OPTS_WITH_VALUE and i + 1 < len(options):
            values[token] = options[i + 1]
            i += 1
        i += 1
    return values


def resolve(raw: str, base: Path) -> Path:
    """Turn a path as typed in the command into one Windows Python can open.

    A Git Bash ``/c/...`` path is rewritten to ``c:/...``; a relative one is taken from
    ``base``.
    """
    given = Path(GIT_BASH_DRIVE.sub(r"\1:\2", raw.strip("'\"")))
    return given if given.is_absolute() else base / given


def repo_for(options: list[str], cwd: Path) -> Path:
    """Return the working tree a commit runs in, given git's own options.

    ``--work-tree`` wins; otherwise the parent of ``--git-dir``, which assumes the usual
    layout and makes git refuse, and so the hook block, when it is not; otherwise ``-C``
    or ``cwd``.
    """
    values = option_values(options)
    base = resolve(values["-C"], cwd) if "-C" in values else cwd
    if "--work-tree" in values:
        return resolve(values["--work-tree"], base)
    if "--git-dir" in values:
        return resolve(values["--git-dir"], base).parent
    return base


def changed_paths(root: Path, include_untracked: bool) -> list[str] | None:
    """Return the paths a commit in ``root`` could write; None if git could not say.

    Staged and unstaged tracked paths, excluding deletions, plus untracked files that
    are not ignored when ``include_untracked`` is set.
    """
    listings = [
        git(root, "diff", "--cached", "--name-only", "--diff-filter=d", "-z"),
        git(root, "diff", "--name-only", "--diff-filter=d", "-z"),
    ]
    if include_untracked:
        listings.append(git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    if any(listing is None for listing in listings):
        return None
    names = b"\0".join(listing for listing in listings if listing is not None)
    return sorted({p for p in names.decode("utf-8", "replace").split("\0") if p})


def versions(root: Path, path: str) -> list[bytes]:
    """Return the index and working copies of ``path``, whichever exist."""
    blobs: list[bytes] = []
    index = git(root, "show", f":{path}")
    if index:
        blobs.append(index)
    try:
        with open(root / path, "rb") as handle:
            blobs.append(handle.read(MAX_SCAN_BYTES + 1))
    except OSError:
        pass
    return blobs


def scan(root: Path, paths: list[str]) -> list[str]:
    """Return one finding per offending path. Names the rule, never the matched value."""
    findings: list[str] = []
    for path in paths:
        by_name = next((label for label, rule in PATH_RULES if rule.search(path)), None)
        if by_name:
            findings.append(f"{path}  <- path looks like a {by_name}")
        if path in CONTENT_EXEMPT:
            continue
        blobs = versions(root, path)
        if any(len(blob) > MAX_SCAN_BYTES for blob in blobs):
            findings.append(f"{path}  <- too large to check for credentials")
            continue
        by_content = next(
            (label for label, rule in CONTENT_RULES if any(rule.search(blob) for blob in blobs)),
            None,
        )
        if by_content:
            findings.append(f"{path}  <- content matches {by_content}")
    return findings


def refuse(reasons: list[str]) -> int:
    """Report why the command is blocked and return the blocking exit code."""
    print("Refusing this commit: it may put a credential into public history.\n", file=sys.stderr)
    for reason in reasons:
        print(f"  {reason}", file=sys.stderr)
    print(
        "\nA later commit does not undo this: the credential stays in every clone. Unstage or "
        "revert the file and rotate the credential if it ever left this machine. If this is a "
        "false positive, tell the operator and let them decide. Do not work around this hook.",
        file=sys.stderr,
    )
    return 2


def run(event: dict[str, Any]) -> int:
    """Apply the guard to one hook event and return the exit code."""
    command = str((event.get("tool_input") or {}).get("command") or "")
    if not LOOKS_LIKE_COMMIT.search(command):
        return 0

    tokens = tokenise(command)
    if tokens is None and re.search(r"\s(-C|--git-dir|--work-tree)\b", command):
        return refuse(["this command could not be parsed, so its repository is unknown"])

    cwd = Path(str(event.get("cwd") or Path.cwd()))
    invocations = commits(tokens) if tokens is not None else []
    roots = [repo_for(options, cwd) for options in invocations]
    # An unparseable command may hide an add, so it is read as if it had one.
    adds = tokens is None or re.search(r"\badd\b", command) is not None

    findings: list[str] = []
    for root in roots or [cwd]:
        paths = changed_paths(root, include_untracked=adds)
        if paths is None:
            if roots:
                return refuse([f"git could not say what {root.name} would commit"])
            continue
        findings += scan(root, paths)
    return refuse(findings) if findings else 0


def main() -> int:
    """Read the hook event from stdin. Any fault in the guard blocks the command."""
    try:
        return run(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - exit 1 would let the command through
        print(f"The commit guard failed ({type(exc).__name__}).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
