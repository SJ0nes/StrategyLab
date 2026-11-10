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


# --------------------------------------------------------------------------
# Where. A command that commits may not say, and git runs only at home.
# --------------------------------------------------------------------------
#
# Git obeys the config of the repository it runs in - `core.fsmonitor` alone
# names a command it will execute - so running git somewhere is trusting it.
# Four versions of this hook tried to work out where a `cd` or a `-C` led and
# each was shown a command it got wrong. This one does not work it out: a
# command that commits and also changes directory, or names a repository, is
# refused whether or not it would have ended up at home.

#: Commands that commit and also move or name a repository. `{other}` is a
#: second repository and `{home}` the one the session is in.
#:
#: Many of these really do commit in `{home}` - the last block is nothing else.
#: They are refused with the rest. That is the price of never guessing.
REDIRECTED = {
    # Aimed at another repository.
    "dash-C": "git -C {other} commit -m x",
    "dash-C-twice": "git -C sub -C {other} commit -m x",
    "cd": "cd {other} && git commit -m x",
    "pushd": "pushd {other} && git commit -m x",
    "Set-Location": "Set-Location {other}; git commit -m x",
    "git-dir-and-work-tree": "git --git-dir {other}/.git --work-tree {other} commit -m x",
    "joined-options": "git --git-dir={other}/.git --work-tree={other} commit -m x",
    "git-dir-alone": "git --git-dir={other}/.git commit -m x",
    "work-tree-alone": "git --work-tree={other} commit -m x",
    "other-git-dir-home-work-tree": "git --git-dir={other}/.git --work-tree=. commit -m x",
    "home-git-dir-other-work-tree": "git --git-dir={home}/.git --work-tree={other} commit -m x",
    "nested-shell": "bash -c 'cd {other} && git commit -m x'",
    # Hidden behind quoting, wrappers or an unexpanded path.
    "quoted-option": 'git "-C" {other} commit -m x',
    "quoted-subcommand": 'git -C {other} "commit" -m x',
    "option-before-dash-C": "git --attr-source HEAD -C {other} commit -m x",
    "split-word": 'c""d {other} && git commit -m x',
    "after-a-hash": "echo a#b; cd {other} && git commit -m x",
    "command-wrapper": "command git -C {other} commit -m x",
    "builtin-wrapper": "builtin cd {other} && git commit -m x",
    "brace-group": "{{ cd {other}; git commit -m x; }}",
    "tilde": "cd ~/elsewhere && git commit -m x",
    "variable": 'cd "$OTHER" && git commit -m x',
    "dash-C-variable": "git -C $HOME/elsewhere commit -m x",
    "bare-cd": "cd && git commit -m x",
    "escaped-space": "cd my\\ dir && git commit -m x",
    "escaped-quotes": 'git commit -m "a \\" ; git -C {other} commit ; \\" b"',
    "script-heredoc": "bash <<EOF\ncd {other}\ngit commit -m x\nEOF",
    "capital-letters": "Set-Location {other}; Git Commit -m x",
    "launcher-chdir": "env --chdir={other} git commit -m x",
    "launcher-working-directory": (
        "Start-Process git -ArgumentList 'commit','-m','y' -WorkingDirectory {other} -Wait"
    ),
    "variable-by-name": "Set-Item env:GIT_DIR {other}/.git; git commit -m x",
    "dollar-quoted": "$'cd' {other} && git commit -m x",
    # Passing through and coming home, or never leaving.
    "subshell": "(cd {other} && ls); git commit -m x",
    "pushd-and-popd": "pushd {other}; ls; popd; git commit -m x",
    "cd-and-back": "cd {other}; cd -; git commit -m x",
    "there-and-home-again": "cd {other} && ls && cd {home} && git commit -m x",
    "into-a-subdirectory": "cd sub && git commit -m x",
    "dash-C-subdirectory": "git -C sub commit -m x",
    "home-by-name": "git --git-dir={home}/.git --work-tree={home} commit -m x",
    "reuse-message": "git commit -C HEAD",
}


def _aimed(form: str, home: Path, other: Path) -> str:
    """Fill one of the REDIRECTED commands with real paths."""
    return REDIRECTED[form].format(home=home.as_posix(), other=other.as_posix())


@pytest.fixture(scope="module")
def fsmonitor_payload(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    """A `core.fsmonitor` value that leaves a marker file, once proven to work here.

    Git runs the command a repository names in `core.fsmonitor` whenever it
    refreshes the index. If the installed git does not, a test built on it would
    pass for the wrong reason, so those tests skip instead.
    """
    root = tmp_path_factory.mktemp("fsmonitor")
    marker = root / "ran.txt"
    payload = f"echo ran > '{marker.as_posix()}' #"
    probe = make_repo(root / "probe")
    git(probe, "config", "core.fsmonitor", payload)
    subprocess.run(["git", "diff", "--name-only"], cwd=probe, capture_output=True, check=False)
    if not marker.exists():
        pytest.skip("this git does not run a core.fsmonitor command")
    marker.unlink()
    return payload, marker


@pytest.mark.parametrize("form", sorted(REDIRECTED))
def test_a_commit_that_also_says_where_is_refused_whatever_anything_holds(
    tmp_path: Path, form: str
) -> None:
    """Both repositories are clean. The refusal is about the command, not the contents."""
    home = make_repo(tmp_path / "home")
    other = make_repo(tmp_path / "other")
    (home / "sub").mkdir()

    done = run_hook(_aimed(form, home, other), home)

    assert done.returncode == 2
    assert "changes directory or names a repository" in done.stderr


@pytest.mark.parametrize("form", sorted(REDIRECTED))
def test_git_is_never_run_in_another_repository(
    tmp_path: Path, form: str, fsmonitor_payload: tuple[str, Path]
) -> None:
    """The other repository names a command for git to run. The hook must not run it."""
    payload, marker = fsmonitor_payload
    home = make_repo(tmp_path / "home")
    other = make_repo(tmp_path / "other")
    (home / "sub").mkdir()
    git(other, "config", "core.fsmonitor", payload)

    assert run_hook(_aimed(form, home, other), home).returncode == 2
    assert not marker.exists()


def test_the_payload_does_run_when_git_is_used_in_that_repository(
    tmp_path: Path, fsmonitor_payload: tuple[str, Path]
) -> None:
    """The control for the test above: the marker appears the moment git looks."""
    payload, marker = fsmonitor_payload
    other = make_repo(tmp_path / "other")
    git(other, "config", "core.fsmonitor", payload)

    subprocess.run(["git", "diff", "--name-only"], cwd=other, capture_output=True, check=False)

    assert marker.exists()
    marker.unlink()


#: Commit messages that talk about moving, in every way a message is written.
#: None of them moves anything.
MESSAGES = {
    "dash-m": 'git commit -m "fix: follow cd and pushd, refuse -C and --git-dir"',
    "single-quoted": "git commit -m 'docs: explain why cd is refused'",
    "names-a-variable": 'git commit -m "docs: why GIT_DIR and --work-tree are refused"',
    "long-option": 'git commit --message "revert the -C handling"',
    "two-paragraphs": 'git commit -m "fix: cd" -m "popd was never modelled"',
    "heredoc": "git commit -F - <<'EOF'\nfix: stop following cd\n\npushd and -C too.\nEOF",
    "heredoc-after-add": "git add a.txt && git commit -q -F - <<EOF\nno more --git-dir\nEOF\n"
    "git log --oneline -1",
}


@pytest.mark.parametrize("form", sorted(MESSAGES))
def test_a_message_that_only_talks_about_moving_is_not_refused(repo: Path, form: str) -> None:
    stage(repo, "a.txt", "fine\n")
    assert run_hook(MESSAGES[form], repo).returncode == 0

    stage(repo, "b.txt", PEM)
    done = run_hook(MESSAGES[form], repo)
    assert done.returncode == 2
    assert "b.txt" in done.stderr, "still scanned, and blocked for what it found"


def test_a_commit_from_a_subdirectory_reads_the_working_copy(repo: Path) -> None:
    """Git lists changed paths from the top level, whatever directory it runs in."""
    (repo / "sub").mkdir()
    (repo / "tracked.txt").write_text(T212)
    assert run_hook("git commit -am x", repo / "sub").returncode == 2


def test_a_work_tree_with_a_separate_git_directory_is_found(tmp_path: Path) -> None:
    """A work tree whose `.git` is a file pointing elsewhere is still a repository."""
    git_dir, work_tree = tmp_path / "store" / "repo.git", tmp_path / "tree"
    work_tree.mkdir()
    git_dir.parent.mkdir()
    git(tmp_path, "init", "-q", f"--separate-git-dir={git_dir}", str(work_tree))

    stage(work_tree, "a.txt", "fine\n")
    assert run_hook("git commit -m x", work_tree).returncode == 0

    stage(work_tree, "b.txt", PEM)
    done = run_hook("git commit -m x", work_tree)
    assert done.returncode == 2
    assert "b.txt" in done.stderr


@pytest.mark.parametrize(
    "prefix",
    [
        "GIT_DIR={git_dir} ",
        "GIT_WORK_TREE={work_tree} ",
        "GIT_INDEX_FILE={git_dir}/index ",
        "export GIT_DIR={git_dir}; ",
        "$env:GIT_DIR = '{git_dir}'; ",
        "git -c core.worktree={work_tree} ",
    ],
)
def test_a_repository_named_through_the_environment_is_refused(tmp_path: Path, prefix: str) -> None:
    dirty = make_repo(tmp_path / "dirty")
    clean = make_repo(tmp_path / "clean")
    named = prefix.format(git_dir=(dirty / ".git").as_posix(), work_tree=dirty.as_posix())
    command = named + ("commit -m x" if named.startswith("git ") else "git commit -m x")

    done = run_hook(command, clean)

    assert done.returncode == 2
    assert "changes directory or names a repository" in done.stderr


def test_a_nested_repository_registered_in_the_index_is_not_entered(
    repo: Path, fsmonitor_payload: tuple[str, Path]
) -> None:
    """`git diff` runs `git status` inside a gitlink unless told not to.

    That inner status obeys the nested repository's config, so a repository
    cloned into the project and added would run its command on every commit.
    """
    payload, marker = fsmonitor_payload
    nested = make_repo(repo / "vendored")
    git(repo, "add", "vendored")
    git(repo, "commit", "-qm", "embed")
    git(nested, "config", "core.fsmonitor", payload)
    (nested / "tracked.txt").write_text("changed\n")

    assert run_hook("git commit -m x", repo).returncode == 0
    assert run_hook("git add a.txt && git commit -m x", repo).returncode == 0
    assert not marker.exists()


@pytest.mark.parametrize("command", ["git status", "echo hello"])
def test_a_command_that_does_not_commit_passes(repo: Path, command: str) -> None:
    stage(repo, "a.txt", PEM)
    assert run_hook(command, repo).returncode == 0


def test_the_words_outside_a_repository_pass(tmp_path: Path) -> None:
    assert run_hook('echo "git commit"', tmp_path).returncode == 0


def test_a_commit_outside_any_repository_has_nothing_to_guard(tmp_path: Path) -> None:
    """Git itself refuses to commit here, and there is no index to read."""
    assert run_hook(COMMIT, tmp_path).returncode == 0


def test_unreadable_input_blocks(tmp_path: Path) -> None:
    assert run_hook("", tmp_path, raw="{not json").returncode == 2
