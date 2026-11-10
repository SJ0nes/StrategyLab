"""Tests for the commit-time credential scan, `.claude/hooks/check_secrets.py`.

The hook is driven the way Claude Code drives it: a JSON event on stdin, the
verdict in the exit code. 0 lets the command run, 2 blocks it, and nothing else
counts - Claude Code logs any other code and lets the command through, so a
guard that crashes with 1 is a guard that is not there.

Every case builds a throwaway git repository under `tmp_path`. No test touches
this repository's own index, and none makes a network call: git runs locally.

The fake credentials are assembled at runtime from two halves. Written whole,
they would trip the scan this file tests the moment the file itself was
committed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "check_secrets.py"

PEM = "-----BEGIN " + "PRIVATE KEY-----\nabc\n"
T212_VALUE = "abcdefghijkl"
T212 = "T212_API_" + f"KEY={T212_VALUE}\n"
ENV_EXAMPLE = "\n".join(
    ["T212_ENVIRONMENT=demo", "T212_API_KEY=", "T212_API_SECRET=", "T212_TICKER=", ""]
)


def git(repo: Path, *args: str) -> None:
    """Run git in `repo`, failing the test if git does."""
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(root: Path) -> Path:
    """Create a repository at `root` with one committed, clean, tracked file."""
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    git(root, "config", "core.autocrlf", "false")
    (root / "tracked.txt").write_text("clean\n")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-qm", "initial")
    return root


def stage(repo: Path, name: str, text: str) -> None:
    """Write `text` to `name` in `repo` and stage it, ignoring any ignore rules."""
    (repo / name).write_text(text)
    git(repo, "add", "-f", name)


def run_hook(command: str, cwd: Path, raw: str | None = None) -> subprocess.CompletedProcess[str]:
    """Feed the hook one PreToolUse event and return the finished process."""
    event = (
        raw
        if raw is not None
        else json.dumps({"tool_input": {"command": command}, "cwd": str(cwd)})
    )
    return subprocess.run(
        [sys.executable, str(HOOK)], input=event, capture_output=True, text=True, timeout=60
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clean repository with nothing staged."""
    return make_repo(tmp_path / "repo")


COMMIT = "git commit -m x"


def test_a_clean_staged_file_passes(repo: Path) -> None:
    stage(repo, "a.txt", "fine\n")
    assert run_hook(COMMIT, repo).returncode == 0


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("a.txt", PEM),
        ("a.txt", T212),
        ("config.json", '{"private_key": "-----' + "BEGIN RSA PRI" + 'VATE KEY-----"}'),
    ],
    ids=["pem-block", "t212-key", "private-key-field"],
)
def test_staged_credential_content_blocks(repo: Path, name: str, text: str) -> None:
    assert run_hook(COMMIT, repo).returncode == 0, "nothing staged yet, so nothing to block"
    stage(repo, name, text)
    assert run_hook(COMMIT, repo).returncode == 2


@pytest.mark.parametrize(
    "name", [".env", ".env.local", "service-account.json", "deploy.pem", "api_token.txt"]
)
def test_credential_shaped_paths_block(repo: Path, name: str) -> None:
    stage(repo, name, "x=1\n")
    assert run_hook(COMMIT, repo).returncode == 2


def test_the_env_example_with_empty_values_passes(repo: Path) -> None:
    """The committed template must not trip its own guard.

    An empty value followed by a newline is where a whitespace class that
    includes the newline would
    step onto the next line and read `T212_API_SECRET=` as an eight-character
    secret.
    """
    stage(repo, ".env.example", ENV_EXAMPLE)
    assert run_hook(COMMIT, repo).returncode == 0


def test_a_refusal_never_prints_the_value(repo: Path) -> None:
    stage(repo, "a.txt", T212)
    done = run_hook(COMMIT, repo)
    assert done.returncode == 2
    assert "a.txt" in done.stderr
    assert T212_VALUE not in done.stderr + done.stdout


@pytest.mark.parametrize("command", ["git commit -am x", "git commit -m x tracked.txt"])
def test_an_unstaged_tracked_change_blocks(repo: Path, command: str) -> None:
    """`-a` and a pathspec both commit files the index does not hold yet."""
    (repo / "tracked.txt").write_text(T212)
    assert run_hook(command, repo).returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "FOO=1 git commit -m x",
        "git status\ngit commit -m x",
        "bash -c 'git commit -m x'",
        'git commit -m "$(cat <<EOF\nan unbalanced " quote\nEOF\n)"',
    ],
    ids=["env-prefix", "multi-line", "shell-wrapped", "unbalanced-quote"],
)
def test_commits_the_parser_cannot_read_are_still_scanned(repo: Path, command: str) -> None:
    stage(repo, "a.txt", PEM)
    assert run_hook(command, repo).returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "git add a.txt && git commit -m x",
        "git add a.txt\ngit commit -m x",
        "git add a.txt; git commit -m x",
    ],
    ids=["and-chained", "multi-line", "semicolon"],
)
def test_a_file_added_in_the_same_command_is_scanned(repo: Path, command: str) -> None:
    """The hook runs before the command, so `git add` has not staged anything yet.

    The file is written and deliberately NOT staged: it is untracked when the
    hook looks, which is exactly the state a chained add-and-commit leaves it in.
    """
    (repo / "a.txt").write_text(T212)
    assert run_hook(command, repo).returncode == 2


def test_an_untracked_file_does_not_block_a_commit_that_adds_nothing(repo: Path) -> None:
    (repo / "notes.txt").write_text(PEM)
    stage(repo, "a.txt", "fine\n")
    assert run_hook(COMMIT, repo).returncode == 0


def test_an_ignored_file_is_not_scanned_by_an_add(repo: Path) -> None:
    (repo / ".gitignore").write_text(".env\n")
    git(repo, "add", ".gitignore")
    (repo / ".env").write_text(T212)
    (repo / "a.txt").write_text("fine\n")
    assert run_hook("git add a.txt && git commit -m x", repo).returncode == 0


@pytest.mark.parametrize("form", ["separate", "joined"])
def test_git_dir_and_work_tree_name_the_repository(tmp_path: Path, form: str) -> None:
    dirty = make_repo(tmp_path / "dirty")
    clean = make_repo(tmp_path / "clean")
    stage(dirty, "a.txt", PEM)
    git_dir, work_tree = (dirty / ".git").as_posix(), dirty.as_posix()
    if form == "separate":
        options = f"--git-dir {git_dir} --work-tree {work_tree}"
    else:
        options = f"--git-dir={git_dir} --work-tree={work_tree}"
    assert run_hook(f"git {options} commit -m x", clean).returncode == 2
    assert run_hook(f"git --git-dir={git_dir} commit -m x", clean).returncode == 2


def test_work_tree_wins_over_the_git_dir_parent(tmp_path: Path) -> None:
    """With a separate git directory, the working tree is not the git dir's parent.

    A clean repository must pass. Reading the parent instead would find no
    repository there and block a commit that carries nothing.
    """
    git_dir, work_tree = tmp_path / "store" / "repo.git", tmp_path / "tree"
    work_tree.mkdir()
    git_dir.parent.mkdir()
    git(tmp_path, "init", "-q", f"--separate-git-dir={git_dir}", str(work_tree))
    stage(work_tree, "a.txt", "fine\n")
    options = f"--git-dir={git_dir.as_posix()} --work-tree={work_tree.as_posix()}"
    assert run_hook(f"git {options} commit -m x", tmp_path).returncode == 0
    stage(work_tree, "b.txt", PEM)
    assert run_hook(f"git {options} commit -m x", tmp_path).returncode == 2


def test_a_commit_from_a_subdirectory_reads_the_working_copy(repo: Path) -> None:
    """Git lists changed paths from the top level, whatever directory it runs in."""
    (repo / "sub").mkdir()
    (repo / "tracked.txt").write_text(T212)
    assert run_hook("git commit -am x", repo / "sub").returncode == 2


@pytest.mark.parametrize("cd", ["cd", "pushd", "Set-Location"])
def test_a_directory_change_before_the_commit_is_followed(tmp_path: Path, cd: str) -> None:
    dirty = make_repo(tmp_path / "dirty")
    clean = make_repo(tmp_path / "clean")
    stage(dirty, "a.txt", PEM)
    assert run_hook(f"{cd} {dirty.as_posix()} && git commit -m x", clean).returncode == 2
    assert run_hook(f"{cd} {clean.as_posix()} && git commit -m x", dirty).returncode == 0


@pytest.mark.parametrize(
    "prefix",
    ["GIT_DIR={git_dir} ", "GIT_WORK_TREE={work_tree} ", "git -c core.worktree={work_tree} "],
)
def test_a_repository_named_another_way_is_refused(tmp_path: Path, prefix: str) -> None:
    """The scan cannot follow these, so it refuses rather than scan the wrong tree."""
    dirty = make_repo(tmp_path / "dirty")
    clean = make_repo(tmp_path / "clean")
    named = prefix.format(git_dir=(dirty / ".git").as_posix(), work_tree=dirty.as_posix())
    command = named + ("commit -m x" if named.startswith("git ") else "git commit -m x")
    assert run_hook(command, clean).returncode == 2


def test_dash_c_scans_the_named_repository(tmp_path: Path) -> None:
    dirty = make_repo(tmp_path / "dirty")
    clean = make_repo(tmp_path / "clean")
    stage(dirty, "a.txt", PEM)
    assert run_hook(f"git -C {dirty.as_posix()} commit -m x", clean).returncode == 2
    assert run_hook(f"git -C {clean.as_posix()} commit -m x", dirty).returncode == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash drive paths exist only on Windows")
def test_dash_c_accepts_a_git_bash_drive_path(tmp_path: Path) -> None:
    dirty = make_repo(tmp_path / "dirty")
    stage(dirty, "a.txt", PEM)
    posix = dirty.as_posix()
    git_bash = "/" + posix[0].lower() + posix[2:]
    assert run_hook(f"git -C {git_bash} commit -m x", tmp_path).returncode == 2


@pytest.mark.parametrize("command", ["git status", "echo hello"])
def test_a_command_that_does_not_commit_passes(repo: Path, command: str) -> None:
    stage(repo, "a.txt", PEM)
    assert run_hook(command, repo).returncode == 0


def test_the_words_outside_a_repository_pass(tmp_path: Path) -> None:
    assert run_hook('echo "git commit"', tmp_path).returncode == 0


def test_an_explicit_commit_outside_a_repository_blocks(tmp_path: Path) -> None:
    assert run_hook(COMMIT, tmp_path).returncode == 2


def test_unreadable_input_blocks(tmp_path: Path) -> None:
    assert run_hook("", tmp_path, raw="{not json").returncode == 2
