"""Thin wrapper around the git command line.

Everything Merge Monkey knows comes from running plain git commands, so the
tool works on any repository without extra setup.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """A git command failed."""

    def __init__(self, args: list[str], code: int, stderr: str):
        self.args_ = args
        self.code = code
        self.stderr = stderr.strip()
        super().__init__(f"git {' '.join(args)} failed ({code}): {self.stderr}")


@dataclass
class Result:
    code: int
    out: str
    err: str


HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")


class Repo:
    def __init__(self, path: str | os.PathLike = "."):
        self.path = Path(path)
        self._sha_cache: dict[str, str | None] = {}

    # -- running commands -------------------------------------------------

    def run_raw(self, *args: str, input: str | None = None, env: dict | None = None) -> Result:
        full_env = None
        if env:
            full_env = dict(os.environ)
            full_env.update(env)
        proc = subprocess.run(
            ["git", *args],
            cwd=self.path,
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=full_env,
        )
        return Result(proc.returncode, proc.stdout, proc.stderr)

    def run(self, *args: str, input: str | None = None, env: dict | None = None) -> str:
        res = self.run_raw(*args, input=input, env=env)
        if res.code != 0:
            raise GitError(list(args), res.code, res.err)
        return res.out

    def ok(self, *args: str) -> bool:
        return self.run_raw(*args).code == 0

    # -- common queries ---------------------------------------------------

    @property
    def git_dir(self) -> Path:
        return Path(self.run("rev-parse", "--absolute-git-dir").strip())

    @property
    def root(self) -> Path:
        return Path(self.run("rev-parse", "--show-toplevel").strip())

    def rev(self, ref: str) -> str | None:
        # Commit ids never change, so they are memoized; branch names are not.
        if HEX_RE.match(ref) and ref in self._sha_cache:
            return self._sha_cache[ref]
        res = self.run_raw("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        sha = res.out.strip() if res.code == 0 else None
        if HEX_RE.match(ref) and sha:
            self._sha_cache[ref] = sha
        return sha

    def must_rev(self, ref: str) -> str:
        sha = self.rev(ref)
        if not sha:
            raise GitError(["rev-parse", ref], 128, f"unknown revision: {ref}")
        return sha

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", a, b)

    def merge_base(self, a: str, b: str) -> str | None:
        res = self.run_raw("merge-base", a, b)
        return res.out.strip() if res.code == 0 else None

    def branch_exists(self, name: str) -> bool:
        return self.rev(name) is not None

    def local_branches(self) -> list[str]:
        out = self.run("for-each-ref", "--format=%(refname:short)", "refs/heads")
        return [b for b in out.splitlines() if b]

    def show_file(self, rev: str, path: str) -> str | None:
        res = self.run_raw("show", f"{rev}:{path}")
        return res.out if res.code == 0 else None

    def config_get_regexp(self, pattern: str) -> list[tuple[str, str]]:
        res = self.run_raw("config", "--get-regexp", pattern)
        if res.code != 0:
            return []
        pairs = []
        for line in res.out.splitlines():
            key, _, value = line.partition(" ")
            pairs.append((key, value))
        return pairs

    def commit_info(self, sha: str) -> "CommitInfo":
        fmt = "%H%x00%an%x00%ae%x00%aI%x00%P%x00%B"
        out = self.run("show", "-s", f"--format={fmt}", sha)
        h, an, ae, date, parents, body = out.split("\x00", 5)
        body = body.rstrip("\n")
        subject = body.splitlines()[0] if body else ""
        return CommitInfo(h, an, ae, date, parents.split(), subject, body)

    def in_progress_operation(self) -> tuple[str, str] | None:
        """Return (operation, their_ref) when a merge/cherry-pick/revert/rebase stopped."""
        gd = self.git_dir
        for name, op in (
            ("CHERRY_PICK_HEAD", "cherry-pick"),
            ("MERGE_HEAD", "merge"),
            ("REVERT_HEAD", "revert"),
            ("REBASE_HEAD", "rebase"),
        ):
            if (gd / name).exists():
                return op, name
        return None


@dataclass
class CommitInfo:
    sha: str
    author: str
    email: str
    date: str
    parents: list[str]
    subject: str
    message: str

    @property
    def short(self) -> str:
        return self.sha[:10]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1
