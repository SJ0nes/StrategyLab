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

A COMMAND THAT COMMITS MAY NOT SAY WHERE. Git is run in the repository the session is
in and nowhere else, because git obeys the config of the repository it runs in -
``core.fsmonitor`` alone names a command it will execute - so running git in a
directory is trusting it. That leaves the question of whether the commit really runs
in the session's repository, and four versions of this hook tried to answer it by
working out where a ``cd``, a ``-C`` or a ``--git-dir`` led. Each was shown a way to be
wrong: a subshell, a ``popd``, an unexpanded ``~``, a quoted option, a ``--git-dir``
paired with an innocent ``--work-tree``. So this version does not work it out. If the
command changes directory at all, or names a repository at all - ``cd``, ``pushd``,
``popd``, ``Set-Location``, ``-C``, ``--git-dir``, ``--work-tree``, ``GIT_DIR`` and its
relatives, ``core.worktree`` - it is refused, whether or not it would have ended up at
home. A plain ``git add ... && git commit ...`` from where the session already is has
nothing to refuse.

The cost is honest commands refused: ``cd sub && git commit``, and ``git commit -C
<commit>``, whose ``-C`` means "reuse that message" and reads the same. What this still
cannot see is anything the words do not show: ``eval``, an alias, a function, a
``$(...)``. It is a guard against an accident, not against an attempt.

The text of a commit message is not a command. A quoted argument to ``-m`` and the body
of a here-document on a line that says ``commit`` are left out of that check, so a
message may describe a ``cd`` without being refused for it. THAT IS A KNOWN HOLE, kept
on purpose: a ``$(cd elsewhere && git commit)`` written inside a message does run, and a
here-document on a commit line can be a script rather than a message. Closing it would
refuse every commit whose message mentions a directory change, and nobody writes
either of those by accident.

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
# setup.md holds a deliberately fake key, the fixture for proving the guards fire. This
# file is not exempt: no pattern here matches its own source, so a key pasted into it
# is caught like any other.
CONTENT_EXEMPT: tuple[str, ...] = (".claude/setup.md",)

# Case-blind: PowerShell runs `Git Commit` as readily as `git commit`.
LOOKS_LIKE_COMMIT = re.compile(r"\bgit(\.exe)?\b[\s\S]*\bcommit\b", re.I)

# Words that move the shell somewhere else, compared in lower case.
DIRECTORY_WORDS = frozenset(
    {"cd", "chdir", "pushd", "popd", "sl", "set-location", "push-location", "pop-location"}
)
# Options that tell git, or the launcher starting it, which directory to use. Lower case.
REPOSITORY_OPTIONS = ("--git-dir", "--work-tree", "--chdir", "-workingdirectory")
# The variables that do the same, as a bare name: `Set-Item env:GIT_DIR` has no equals.
REPOSITORY_NAMES = re.compile(r"GIT_(DIR|WORK_TREE|INDEX_FILE|COMMON_DIR)", re.I)
# Environment and config that do the same. Matched anywhere in the command, message
# included: a variable assignment can sit in places a tokenizer does not look.
REPOSITORY_SETTINGS = re.compile(
    r"\bGIT_(DIR|WORK_TREE|INDEX_FILE|COMMON_DIR)\s*=|\bcore\.(worktree|bare)\b", re.I
)
# Options whose next argument is the commit's message, which is text and not a command.
MESSAGE_OPTIONS = frozenset({"-m", "--message"})
# A here-document: `<<TAG`, `<<'TAG'`, `<<"TAG"` or `<<-TAG`, capturing the tag.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
QUOTES = str.maketrans("", "", "'\"`\\")

# Without this, ``git diff`` runs ``git status`` inside every nested repository the index
# registers, and that inner status obeys the nested repository's own config.
NO_SUBMODULES = ("--ignore-submodules=all",)

# Bytes. Past this a file is refused unread rather than half-checked.
MAX_SCAN_BYTES = 50_000_000


def git(root: Path, *args: str) -> bytes | None:
    """Run git in ``root``; return stdout, or None if git could not answer."""
    try:
        done = subprocess.run(["git", *args], cwd=str(root), capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def without_message_bodies(command: str) -> str:
    """Return ``command`` with the body of every here-document that feeds a commit removed.

    ``git commit -F - <<'EOF'`` is how a long message is written, and its body is prose.
    Only a here-document on a line that says ``commit`` is treated that way: one feeding
    ``bash`` is a script, and its ``cd`` is as real as any other.
    """
    kept: list[str] = []
    closing: str | None = None
    for line in command.split("\n"):
        if closing is not None:
            if line.strip() == closing:
                closing = None
            continue
        kept.append(line)
        opened = HEREDOC.search(line)
        if opened and re.search(r"\bcommit\b", line):
            closing = opened.group(2)
    return "\n".join(kept)


def words(text: str) -> list[str]:
    """Split shell text into words, opening up quoted text that is not a commit message.

    Quotes do not hide a word here. ``"-C"`` is ``-C``, ``c""d`` is ``cd``, and the
    string handed to ``bash -c`` is split in its turn. The one quoted thing left whole
    is the argument after ``-m``, which is a message. A ``#`` is never a comment: the
    shell only treats it as one at the start of a word, and guessing wrong would hide
    everything after it.
    """
    try:
        lexer = shlex.shlex(text, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        # Unbalanced quotes. Split on anything that is not part of a word instead, so
        # nothing is skipped; a message may be split with it, which only costs a refusal.
        tokens = re.split(r"[\s;&|(){}]+", text)

    found: list[str] = []
    after_message_option = False
    for token in tokens:
        quoted = len(token) >= 2 and token[0] in "'\"" and token[-1] == token[0]
        bare = token.translate(QUOTES)
        if quoted and after_message_option:
            pass
        elif quoted and re.search(r"\s", bare):
            found += words(token[1:-1])
        else:
            found.append(bare)
        after_message_option = bare in MESSAGE_OPTIONS
    return found


def redirections(command: str) -> list[str]:
    """Return every word in ``command`` that moves the shell or names a repository."""
    text = without_message_bodies(command)
    found = [match.group(0).strip() for match in REPOSITORY_SETTINGS.finditer(command)]
    for word in words(text):
        # `$'cd'` is bash for the string cd, so a leading dollar hides nothing.
        plain = word.lstrip("$").lower()
        if plain in DIRECTORY_WORDS or word == "-C":
            found.append(word)
        elif plain.startswith(REPOSITORY_OPTIONS):
            found.append(word.split("=", 1)[0])
        elif named := REPOSITORY_NAMES.search(word):
            found.append(named.group(0))
    return sorted(set(found))


def is_repo_root(directory: Path) -> bool:
    """Say whether git, run in ``directory``, would take it for a repository of its own.

    Either it holds a ``.git`` entry, or it IS a git directory: one holding ``HEAD`` and
    ``objects``.
    """
    if (directory / ".git").exists():
        return True
    return (directory / "HEAD").is_file() and (directory / "objects").is_dir()


def enclosing_repo(directory: Path) -> Path | None:
    """Return the nearest directory at or above ``directory`` that is a repository root.

    Found by LOOKING, never by asking git, so that nothing is run in a directory before
    it is known to be the session's own.
    """
    try:
        start = directory.resolve()
        return next((d for d in (start, *start.parents) if is_repo_root(d)), None)
    except OSError:
        return None


def changed_paths(root: Path, include_untracked: bool) -> list[str] | None:
    """Return the paths a commit in ``root`` could write; None if git could not say.

    Staged and unstaged tracked paths, excluding deletions, plus untracked files that
    are not ignored when ``include_untracked`` is set.
    """
    listings = [
        git(root, "diff", "--cached", "--name-only", "--diff-filter=d", "-z", *NO_SUBMODULES),
        git(root, "diff", "--name-only", "--diff-filter=d", "-z", *NO_SUBMODULES),
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

    moved = redirections(command)
    if moved:
        return refuse([
            f"the command commits and also changes directory or names a repository "
            f"({', '.join(moved)}). This guard runs git only in the repository the session "
            "is in, and it does not try to work out where such a command ends up: every "
            "version that tried was shown a way to be wrong. Run the commit as a plain "
            "`git add ... && git commit ...` from the directory the session is already in"
        ])

    home = enclosing_repo(Path(str(event.get("cwd") or Path.cwd())))
    if home is None:
        # Not in a repository, so git has nothing to commit to and there is nothing to read.
        return 0

    paths = changed_paths(home, re.search(r"\badd\b", command) is not None)
    if paths is None:
        return refuse([f"git could not say what {home.name} would commit"])
    findings = scan(home, paths)
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
