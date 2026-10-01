"""The harness git repository of one evolution run (candidate isolation, incumbent history).

Layout under the run directory::

    <run>/harness_repo/            git repository; the harness package lives in ``harness/``
    <run>/wt/<name>/harness/       worktrees (one per candidate, incumbent, held-out check)

The run evolves on branch ``evolve/<name>``. Every candidate of round t is drafted in its own
worktree on branch ``<name>/r{t}{variant}`` from the incumbent, so variants never see each
other's edits and can be evaluated concurrently (R-Alg2 "in parallel"). Accepting a candidate
fast-forwards ``evolve/<name>``. The frontier records the git tree of ``harness/`` so commits
outside the harness never look like a change (reference ``rrsi/gitops.py``).

Commits are authored with a repository-local identity passed on every command (``-c``) and also
written to the repository's own ``.git/config`` — never to the global configuration. The identity
defaults to ``xgen-rsi <xgen-rsi@users.noreply.github.com>`` and can be overridden with the
environment variables ``XGEN_RSI_GIT_AUTHOR_NAME`` / ``XGEN_RSI_GIT_AUTHOR_EMAIL`` (read each time
a git command runs).

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/gitops.py``), Copyright 2026 The
rrsi Authors / Google LLC, Apache License 2.0; modified by PlateerLab.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

AUTHOR_NAME = "xgen-rsi"
AUTHOR_EMAIL = "xgen-rsi@users.noreply.github.com"
AUTHOR_NAME_ENV = "XGEN_RSI_GIT_AUTHOR_NAME"
AUTHOR_EMAIL_ENV = "XGEN_RSI_GIT_AUTHOR_EMAIL"
HARNESS = "harness"


def author_identity() -> tuple[str, str]:
    """(name, email) for harness-repo commits: the environment override, else the default."""
    name = os.environ.get(AUTHOR_NAME_ENV, "").strip() or AUTHOR_NAME
    email = os.environ.get(AUTHOR_EMAIL_ENV, "").strip() or AUTHOR_EMAIL
    return name, email


def _opts() -> List[str]:
    name, email = author_identity()
    return ["-c", f"user.name={name}", "-c", f"user.email={email}",
            "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]


_SCRUB_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
              "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES")


class GitError(RuntimeError):
    pass


def git(cwd: Path | str, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    r = subprocess.run(["git", *_opts(), *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {r.stderr[-800:]}")
    return r


class HarnessRepo:
    """Git plumbing for one run's harness repository."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    # ---------------------------------------------------------------- setup --
    def exists(self) -> bool:
        return (self.root / ".git").exists()

    def init(self, start_harness: Path | str, branch: str, *, label: str = "H_0") -> None:
        """Create the repository with ``start_harness`` copied to ``harness/`` (idempotent) and
        make sure ``branch`` exists."""
        if not self.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            git(self.root, "init", "-q", "-b", "main", check=True)
            name, email = author_identity()
            for key, value in (("user.name", name), ("user.email", email),
                               ("commit.gpgsign", "false")):
                git(self.root, "config", "--local", key, value, check=True)
            dest = self.root / HARNESS
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(Path(start_harness), dest,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "__init__.py"))
            git(self.root, "add", "-A", "--", HARNESS, check=True)
            git(self.root, "commit", "-q", "-m", f"{label}: starting harness", check=True)
        self.ensure_branch(branch, "main")

    def harness_dir(self, worktree: Path | str) -> Path:
        return Path(worktree) / HARNESS

    # ----------------------------------------------------------------- refs --
    def rev(self, ref: str) -> str:
        r = git(self.root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if r.returncode != 0:
            raise GitError(f"unknown ref {ref!r}")
        return r.stdout.strip()

    def tree_hash(self, ref: str) -> str:
        """Git tree of ``harness/`` at ``ref``."""
        r = git(self.root, "rev-parse", f"{ref}:{HARNESS}")
        if r.returncode != 0:
            raise GitError(f"no {HARNESS}/ at {ref!r}: {r.stderr.strip()}")
        return r.stdout.strip()

    def branch_exists(self, name: str) -> bool:
        return bool(name) and git(self.root, "rev-parse", "--verify", "--quiet",
                                  f"refs/heads/{name}").returncode == 0

    def ensure_branch(self, name: str, start: str = "HEAD") -> None:
        if not self.branch_exists(name):
            git(self.root, "branch", name, start, check=True)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return git(self.root, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0

    def fast_forward(self, branch: str, commit: str) -> None:
        """Move ``refs/heads/<branch>`` to ``commit`` if that is a fast-forward."""
        if not self.is_ancestor(branch, commit):
            raise GitError(f"{commit} is not a fast-forward of {branch}")
        self.update_ref(branch, commit)

    def update_ref(self, branch: str, commit: str) -> None:
        git(self.root, "update-ref", f"refs/heads/{branch}", commit, check=True)

    # ------------------------------------------------------------ worktrees --
    def worktree_new_branch(self, path: Path, branch: str, start: str) -> Path:
        """Fresh worktree on a NEW branch ``branch`` at ``start`` (an old one is removed)."""
        self.worktree_remove(path, branch)
        path.parent.mkdir(parents=True, exist_ok=True)
        git(self.root, "worktree", "add", "-q", "-b", branch, str(path), start, check=True)
        return path

    def worktree_checkout(self, path: Path, branch: str) -> Path:
        """Worktree on the EXISTING ``branch`` (an old worktree at ``path`` is removed)."""
        self.worktree_remove(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        git(self.root, "worktree", "add", "-q", str(path), branch, check=True)
        return path

    def worktree_detached(self, path: Path, ref: str) -> Path:
        self.worktree_remove(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        git(self.root, "worktree", "add", "-q", "--detach", str(path), ref, check=True)
        return path

    def worktree_remove(self, path: Path, branch: Optional[str] = None) -> None:
        if path.exists():
            git(self.root, "worktree", "remove", "--force", str(path))
            shutil.rmtree(path, ignore_errors=True)
        git(self.root, "worktree", "prune")
        if branch and self.branch_exists(branch):
            git(self.root, "branch", "-D", branch)

    # ---------------------------------------------------------------- diffs --
    def diff_with_new_files(self, worktree: Path) -> str:
        """Working-tree diff of ``harness/`` including the full text of new files."""
        git(worktree, "add", "-N", "--", HARNESS)
        return git(worktree, "diff", "--", HARNESS).stdout

    def diff_refs(self, a: str, b: str) -> str:
        return git(self.root, "diff", a, b, "--", HARNESS).stdout

    def harness_version(self, ref: str) -> str:
        """Manifest version id (``sha256:…``) of the harness at ``ref``."""
        from xgen_rsi.harness.spec import load_manifest

        path = self.root.parent / f".version_{ref.replace('/', '_')[:40]}"
        self.worktree_detached(path, ref)
        try:
            return load_manifest(self.harness_dir(path)).version_id()
        finally:
            self.worktree_remove(path)

    def changed_paths(self, worktree: Path) -> List[str]:
        git(worktree, "add", "-N", "--", HARNESS)
        out = git(worktree, "diff", "--name-only", "--", HARNESS).stdout
        return [ln for ln in out.split("\n") if ln.strip()]

    def commit(self, worktree: Path, message: str) -> str:
        git(worktree, "add", "-A", "--", HARNESS, check=True)
        git(worktree, "commit", "-q", "--allow-empty", "-m", message, check=True)
        return git(worktree, "rev-parse", "HEAD", check=True).stdout.strip()


def diff_dirs(a: Path | str, b: Path | str) -> str:
    """Unified diff between two harness directories (e.g. incumbent and candidate checkouts)."""
    return git(Path(a).parent, "diff", "--no-index", "--no-color", "--", str(a), str(b)).stdout
